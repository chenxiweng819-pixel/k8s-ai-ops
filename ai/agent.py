"""K8s 专用 AI 助手 Agent。

工作方式：
1. 每轮对话先注入一份「集群实时快照」（总览 + 异常清单），保证模型即使不调用
   工具也有事实依据，不会凭空编造；
2. 再用 function calling 让模型按需下钻（看日志、看事件、看 YAML）；
3. 只读工具立即执行；写工具不执行，返回待确认动作交给前端弹窗；
4. 没有配置 API Key 时，退化为本地规则分析（基于 diagnose + knowledge），
   仍然能给出可用的异常分析与修复建议。
"""
from __future__ import annotations

import json
import logging

import requests

from backend import audit
from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT

from . import knowledge, tools

log = logging.getLogger("ai.agent")

MAX_STEPS = 6

SYSTEM_PROMPT = """你是「K8s 智能运维助手」，深度集成在一个 K8s 管理平台里，服务于熟悉 Kubernetes 的运维与研发工程师。

你的能力边界：
- 你可以调用工具读取集群真实数据（资源列表、YAML、事件、日志、节点详情、Service 后端）。
- 你可以调用写操作工具（扩缩容、重启、删除、cordon/uncordon、容器内执行命令），但这些操作**不会立即生效**，平台会向用户弹窗确认后才执行。所以你可以提议操作，但要说明理由和影响。
- 你无法修改集群外的东西，也无法访问平台未暴露的资源。

回答要求：
1. **先结论后细节**：开头一句话说清「是什么问题」，再展开。
2. **必须基于证据**：引用具体对象名、命名空间、事件内容、日志片段或状态字段。不要编造资源名或日志。
3. **给出可执行方案**：按「立即止血 → 根治 → 预防」三个层次给建议，写清具体命令或要改的 YAML 字段。
4. **区分确定与推测**：证据不足时明确说「需要进一步确认」，并说明要查什么。
5. **中文回答**，用 Markdown；YAML/命令用代码块。表格适合对比多个异常对象。
6. 涉及删除、缩容、重启等破坏性操作时，必须先提示风险与影响范围（例如会中断服务、会丢数据）。
7. 与 K8s 或本平台无关的问题，礼貌拒绝并把话题拉回运维场景。

排查心法：Pod 层面异常 90% 出自镜像、配置、资源、探针这四类；先看事件（describe 的 Events 段通常直接给根因），再看日志（崩溃类必须看 --previous）。"""


# ---------------------------------------------------------------------------
# 集群快照（每轮对话的事实底座）
# ---------------------------------------------------------------------------
def cluster_snapshot(namespace=None):
    impl, mode = tools._impl()
    ov = impl.cluster_overview()

    return {
        "mode": mode,
        "context": ov.get("context"),
        "demo": ov.get("demo", False),
        "demoReason": ov.get("demoReason"),
        "counts": ov.get("counts"),
        "podPhases": ov.get("podPhases"),
        "abnormalPods": ov.get("abnormalPods", [])[:25],
        "abnormalWorkloads": ov.get("abnormalWorkloads", [])[:20],
        "abnormalNodes": ov.get("abnormalNodes", []),
        "abnormalCount": ov.get("abnormalCount"),
    }


def _snapshot_text(snap):
    lines = []
    if snap.get("demo"):
        lines.append(
            f"注意：当前后端处于【演示模式】，数据是内置模拟集群，不是真实集群。"
            f"原因：{snap.get('demoReason')}。真实集群需要把 kubeconfig 放到 D:\\config 或在平台里导入。"
        )
    lines.append(f"集群 context：{snap.get('context')}")
    c = snap.get("counts") or {}
    lines.append(
        f"规模：节点 {c.get('nodes')}（就绪 {c.get('readyNodes')}）/ 命名空间 {c.get('namespaces')} / "
        f"Pod {c.get('pods')} / Deployment {c.get('deployments')} / Service {c.get('services')}"
    )
    lines.append(f"Pod 相位分布：{json.dumps(snap.get('podPhases') or {}, ensure_ascii=False)}")
    lines.append(f"异常对象总数：{snap.get('abnormalCount')}")

    pods = snap.get("abnormalPods") or []
    if pods:
        lines.append("\n【异常 Pod】")
        for p in pods[:25]:
            types = ",".join(i["type"] for i in p.get("issues", []))
            lines.append(f"- {p.get('namespace')}/{p.get('name')} [{types}] node={p.get('node')}")

    wls = snap.get("abnormalWorkloads") or []
    if wls:
        lines.append("\n【异常工作负载】")
        for w in wls[:15]:
            types = ",".join(i["type"] for i in w.get("issues", []))
            lines.append(f"- {w.get('namespace')}/{w.get('name')} ({w.get('kind')}) [{types}]")

    nodes = snap.get("abnormalNodes") or []
    if nodes:
        lines.append("\n【异常节点】")
        for n in nodes[:10]:
            types = ",".join(i["type"] for i in n.get("issues", []))
            lines.append(f"- {n.get('name')} [{types}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 本地规则分析（无 API Key 时的兜底，仍然给出真实结论）
# ---------------------------------------------------------------------------
def local_analysis(message, namespace=None):
    """不依赖大模型的诊断：用规则引擎 + 知识库产出结论。"""
    scan = tools.tool_scan_cluster({"namespace": namespace})
    findings = scan.get("findings", [])

    if not findings:
        return (
            "### 巡检结论\n"
            "当前没有检测到处于异常状态的 Pod、工作负载或节点。\n\n"
            f"（数据来源：{'内置演示集群' if scan.get('mode') == 'demo' else '真实集群'}）"
        )

    by_type = {}
    for f in findings:
        for i in f["issues"]:
            by_type.setdefault(i["type"], []).append(f)

    lines = [
        "### 巡检结论",
        f"共发现 **{len(findings)}** 个异常对象，其中严重 {scan.get('critical', 0)} 个。"
        f"（{'演示集群' if scan.get('mode') == 'demo' else '真实集群'}，未接入大模型，以下为规则引擎输出）",
        "",
        "### 问题分类",
    ]
    for etype, objs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        entry = knowledge.lookup(etype) or {}
        lines.append(f"\n#### {etype}（{len(objs)} 个）")
        if entry.get("meaning"):
            lines.append(f"**含义**：{entry['meaning']}")
        lines.append("受影响对象：")
        for o in objs[:8]:
            lines.append(f"- `{o.get('namespace') or '-'}/{o.get('name')}` ({o.get('kind')})")
        if entry.get("causes"):
            lines.append("**常见根因**：")
            lines.extend(f"- {c}" for c in entry["causes"][:4])
        if entry.get("checks"):
            lines.append("**排查命令**：")
            lines.extend(f"- `{c}`" for c in entry["checks"][:3])
        if entry.get("fixes"):
            lines.append("**修复方案**：")
            lines.extend(f"- {f}" for f in entry["fixes"][:4])

    lines.append("\n### 手工排查的统一顺序")
    lines.append(knowledge.GENERIC_PLAYBOOK.strip())
    lines.append(
        "\n> 未检测到可用的模型 API Key，以上为规则引擎结论。"
        "配置 `DEEPSEEK_API_KEY` 后可获得结合日志与事件的深度分析。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------
def _chat_completion(messages, use_tools=True):
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,  # 运维场景要稳定可复现，不要发散
        "stream": False,
    }
    if use_tools:
        payload["tools"] = tools.TOOL_SCHEMAS
        payload["tool_choice"] = "auto"

    resp = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=LLM_TIMEOUT,
    )
    if resp.status_code >= 400:
        detail = resp.text[:400]
        try:
            detail = resp.json().get("error", {}).get("message") or detail
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"模型接口返回 {resp.status_code}: {detail}")
    return resp.json()


# ---------------------------------------------------------------------------
# Agent 主循环
# ---------------------------------------------------------------------------
def run(message, history=None, namespace=None, focus=None, actor="ai"):
    """处理一次对话，返回 {answer, steps, pendingActions, mode, usage}。"""
    snap = cluster_snapshot(namespace)
    snapshot_text = _snapshot_text(snap)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for h in (history or [])[-10:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:4000]})

    user_block = [f"【当前集群实时快照】\n{snapshot_text}"]
    if focus:
        user_block.append(
            f"\n【用户当前关注的对象】{focus.get('kind')} {focus.get('namespace') or ''}/{focus.get('name')}"
        )
    user_block.append(f"\n【用户问题】\n{message}")
    messages.append({"role": "user", "content": "\n".join(user_block)})

    # 没有 Key → 本地规则分析
    if not LLM_API_KEY:
        audit.record("ai-chat", actor=actor, source="ai-agent", success=True,
                     message="local-rule-analysis (no api key)")
        return {
            "answer": local_analysis(message, namespace),
            "steps": [{"type": "notice", "text": "未配置 DEEPSEEK_API_KEY，已切换到本地规则引擎分析"}],
            "pendingActions": [],
            "mode": "local",
            "snapshot": snap,
        }

    steps = []
    pending_actions = []
    usage = {}

    try:
        for _ in range(MAX_STEPS):
            data = _chat_completion(messages)
            usage = data.get("usage") or usage
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                answer = (msg.get("content") or "").strip() or "（模型未返回内容）"
                audit.record("ai-chat", actor=actor, source="ai-agent", success=True,
                             message=message[:200], detail={"model": LLM_MODEL})
                return {
                    "answer": answer,
                    "steps": steps,
                    "pendingActions": pending_actions,
                    "mode": "llm",
                    "snapshot": snap,
                    "usage": usage,
                }

            # 有工具调用：把 assistant 的 tool_calls 原样回填，再补 tool 结果
            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name in tools.WRITE_TOOLS:
                    action = tools.build_pending_action(name, args)
                    pending_actions.append(action)
                    steps.append({"type": "action", "tool": name, "args": args,
                                  "text": f"提议操作：{action['description']}"})
                    result = {
                        "status": "awaiting_user_confirmation",
                        "message": "该操作已提交给用户确认，尚未执行。请基于此继续回答，并说明该操作的影响与风险。",
                        "description": action["description"],
                    }
                else:
                    result = tools.run_readonly(name, args, actor=actor)
                    steps.append({"type": "tool", "tool": name, "args": args,
                                  "text": f"已调用 {name}"})

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:20000],
                })

        # 超过步数上限，强制收口
        messages.append({
            "role": "user",
            "content": "已达到工具调用步数上限，请直接基于目前掌握的信息给出结论与建议。",
        })
        data = _chat_completion(messages, use_tools=False)
        answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "（分析未完成）"
        return {"answer": answer.strip(), "steps": steps, "pendingActions": pending_actions,
                "mode": "llm", "snapshot": snap, "usage": data.get("usage") or usage}

    except requests.exceptions.Timeout:
        return _degraded("模型响应超时", message, namespace, steps, pending_actions, snap)
    except Exception as exc:  # noqa: BLE001
        log.exception("Agent 调用失败")
        return _degraded(str(exc), message, namespace, steps, pending_actions, snap)


def _degraded(reason, message, namespace, steps, pending_actions, snap):
    """模型不可用时的降级：仍然给规则引擎结论，并说明原因。"""
    steps.append({"type": "notice", "text": f"模型调用失败（{reason}），已降级为本地规则分析"})
    return {
        "answer": f"> ⚠️ 模型调用失败：{reason}\n\n已自动降级为本地规则引擎分析：\n\n" + local_analysis(message, namespace),
        "steps": steps,
        "pendingActions": pending_actions,
        "mode": "degraded",
        "snapshot": snap,
    }


def analyze_object(resource, name, namespace=None, actor="ai"):
    """一键诊断：针对单个对象给出「问题定性 + 证据 + 方案」。"""
    diag = tools.tool_diagnose_object({"resource": resource, "name": name, "namespace": namespace})

    if not diag.get("issues"):
        return {
            "healthy": True,
            "summary": f"{diag['kind']} {namespace or ''}/{name} 未发现异常。",
            "issues": [],
            "diag": diag,
        }

    if not LLM_API_KEY:
        return {
            "healthy": False,
            "summary": f"检测到 {len(diag['issues'])} 项异常，未配置 API Key，以下为规则引擎结论。",
            "issues": diag["issues"],
            "playbook": diag.get("playbook"),
            "diag": diag,
            "mode": "local",
        }

    prompt = (
        f"请诊断 {diag['kind']} `{namespace or '-'}/{name}`，以下是平台采集到的证据。"
        "要求：1) 用一句话定性是什么问题；2) 列出证据与推断链；3) 给出立即止血、根治、预防三层方案，"
        "命令要具体；4) 指出还需要补充确认的信息。"
    )
    result = run(prompt, namespace=namespace, focus={"kind": diag["kind"], "name": name, "namespace": namespace},
                 actor=actor)
    result["healthy"] = False
    result["issues"] = diag["issues"]
    result["summary"] = f"检测到 {len(diag['issues'])} 项异常"
    result["diag"] = diag
    return result
