"""AI 可调用的工具集。

关键设计：
1. 工具直接调用 backend 的 service 层（而非 HTTP），所以 AI 的操作与界面按钮
   走**完全相同**的校验、诊断与审计逻辑；
2. 工具分两类：只读工具直接执行；写工具不直接执行，而是返回一个
   「待确认动作」（pending action），由前端弹窗让用户二次确认后才真正生效。
   这样既满足「AI 能操作平台」，又不会让模型把生产环境删了。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import audit  # noqa: E402
from backend.diagnose import node_issues, pod_issues, workload_issues  # noqa: E402
from backend.kube import RESOURCES, get_client, resolve  # noqa: E402
from backend.registry import K8sError  # noqa: E402

from . import knowledge  # noqa: E402


# ---------------------------------------------------------------------------
# 工具定义（OpenAI function calling schema）
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_cluster_overview",
            "description": "获取集群总览：节点/命名空间/Pod/Deployment/Service 数量、Pod 相位分布、异常对象统计。排查任何问题前建议先调用它建立全局视图。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_cluster",
            "description": "全集群异常巡检，返回所有异常的节点、工作负载与 Pod 及其具体原因。用户说「集群有什么问题」「帮我巡检」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "限定命名空间，不传则扫描全集群"},
                    "severity": {"type": "string", "enum": ["all", "critical", "warning"], "description": "按严重级别过滤，默认 all"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_resources",
            "description": "按类型列举资源。resource 支持 pods/deployments/statefulsets/daemonsets/services/ingresses/configmaps/secrets/nodes/namespaces/persistentvolumeclaims/persistentvolumes/events/jobs/cronjobs 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "资源类型，如 pods"},
                    "namespace": {"type": "string", "description": "命名空间，不传则跨命名空间"},
                    "label_selector": {"type": "string", "description": "标签选择器，如 app=nginx"},
                },
                "required": ["resource"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_resource",
            "description": "获取单个资源的完整 YAML（已剔除 managedFields 等噪音），用于核对配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                },
                "required": ["resource", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "获取 Pod 容器日志。排查崩溃类问题务必用 previous=true 看上一次崩溃的日志（当前日志常常是空的）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "container": {"type": "string"},
                    "tail": {"type": "integer", "description": "末尾行数，默认 200"},
                    "previous": {"type": "boolean", "description": "是否取上一次实例的日志"},
                },
                "required": ["namespace", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_events",
            "description": "获取事件。可按命名空间或对象名过滤，Warning 事件通常直接指出根因。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "object_name": {"type": "string", "description": "按对象名过滤，如某个 Pod 名"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_object",
            "description": "对单个对象做深度诊断：聚合异常判定、相关事件、容器状态与日志，并给出结构化结论。用户问「这个 Pod 怎么了」时优先调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                },
                "required": ["resource", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_detail",
            "description": "查看节点的资源分配、污点、Conditions 与运行的 Pod 数量。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_endpoints",
            "description": "检查 Service 是否有可用后端（Endpoints）。用于诊断「服务访问不通 / 502」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                },
                "required": ["name", "namespace"],
            },
        },
    },
    # ---------------- 写操作：不直接执行，返回待确认动作 ----------------
    {
        "type": "function",
        "function": {
            "name": "scale_workload",
            "description": "【写操作，需要用户确认】调整 Deployment/StatefulSet 的副本数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "deployments 或 statefulsets"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                    "replicas": {"type": "integer"},
                },
                "required": ["resource", "name", "namespace", "replicas"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_workload",
            "description": "【写操作，需要用户确认】滚动重启 Deployment/StatefulSet/DaemonSet，等价于 kubectl rollout restart。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                },
                "required": ["resource", "name", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_resource",
            "description": "【写操作，需要用户确认，高危】删除资源。除非用户明确要求删除，否则不要调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string"},
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                },
                "required": ["resource", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cordon_node",
            "description": "【写操作，需要用户确认】把节点标记为不可调度（cordon）或恢复调度（uncordon）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "action": {"type": "string", "enum": ["cordon", "uncordon"]},
                },
                "required": ["name", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_in_pod",
            "description": "【写操作，需要用户确认】在容器内执行一次性命令（非交互），如 nslookup、curl、env、ps。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "command": {"type": "string", "description": "完整命令字符串"},
                    "container": {"type": "string"},
                },
                "required": ["namespace", "name", "command"],
            },
        },
    },
]

WRITE_TOOLS = {"scale_workload", "restart_workload", "delete_resource", "cordon_node", "exec_in_pod"}

# 写操作的风险等级，供前端提示语使用
RISK = {
    "scale_workload": "medium",
    "restart_workload": "medium",
    "cordon_node": "medium",
    "exec_in_pod": "high",
    "delete_resource": "critical",
}


# ---------------------------------------------------------------------------
# 只读工具实现
# ---------------------------------------------------------------------------
def _impl():
    impl, mode = get_client()
    return impl, mode


def _brief(obj, kind=None):
    """把资源对象压缩成模型易读的精简结构，避免塞满整个 YAML 撑爆上下文。"""
    m = obj.get("metadata") or {}
    s = obj.get("status") or {}
    spec = obj.get("spec") or {}
    k = kind or obj.get("kind")

    base = {"kind": k, "name": m.get("name"), "namespace": m.get("namespace")}

    if k == "Pod":
        cs = s.get("containerStatuses") or []
        base.update({
            "phase": s.get("phase"),
            "node": spec.get("nodeName"),
            "podIP": s.get("podIP"),
            "restarts": sum(c.get("restartCount", 0) for c in cs),
            "containers": [
                {
                    "name": c.get("name"),
                    "image": c.get("image"),
                    "ready": c.get("ready"),
                    "restarts": c.get("restartCount"),
                    "reason": (c.get("state") or {}).get("waiting", {}).get("reason")
                              or (c.get("state") or {}).get("terminated", {}).get("reason")
                              or (c.get("lastState") or {}).get("terminated", {}).get("reason"),
                }
                for c in cs
            ],
            "issues": pod_issues(obj),
        })
    elif k == "Node":
        base.update({
            "ready": next(
                (c.get("status") for c in (s.get("conditions") or []) if c.get("type") == "Ready"), None
            ),
            "unschedulable": spec.get("unschedulable", False),
            "kubeletVersion": (s.get("nodeInfo") or {}).get("kubeletVersion"),
            "capacity": s.get("capacity"),
            "taints": spec.get("taints"),
            "issues": node_issues(obj),
        })
    elif k in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
        desired = spec.get("replicas", s.get("desiredNumberScheduled"))
        base.update({
            "desired": desired,
            "ready": s.get("readyReplicas", s.get("numberReady")),
            "available": s.get("availableReplicas", s.get("numberAvailable")),
            "updated": s.get("updatedReplicas", s.get("updatedNumberScheduled")),
            "image": next(
                (c.get("image") for c in ((spec.get("template") or {}).get("spec") or {}).get("containers", [])),
                None,
            ),
            "issues": workload_issues(obj),
        })
    elif k == "Service":
        base.update({
            "type": spec.get("type"),
            "clusterIP": spec.get("clusterIP"),
            "ports": [
                {"port": p.get("port"), "targetPort": p.get("targetPort"), "nodePort": p.get("nodePort")}
                for p in spec.get("ports", [])
            ],
            "selector": spec.get("selector"),
        })
    elif k in ("PersistentVolumeClaim", "PersistentVolume"):
        base.update({
            "phase": s.get("phase"),
            "capacity": s.get("capacity") or (spec.get("resources") or {}).get("requests"),
            "storageClass": spec.get("storageClassName"),
            "volumeName": spec.get("volumeName"),
        })
    elif k == "Event":
        base.update({
            "type": obj.get("type"), "reason": obj.get("reason"),
            "message": obj.get("message"), "count": obj.get("count"),
            "object": obj.get("involvedObject", {}).get("name"),
            "lastTimestamp": obj.get("lastTimestamp"),
        })
    else:
        base.update({"summary": "已省略大字段，可用 get_resource 查看完整内容"})
        if k == "ConfigMap":
            base["keys"] = list((obj.get("data") or {}).keys())
        if k == "Secret":
            base["type"] = obj.get("type")
            base["keys"] = list((obj.get("data") or {}).keys())
    return base


def tool_get_cluster_overview(args):
    impl, mode = _impl()
    ov = impl.cluster_overview()
    return {"mode": mode, **ov}


def tool_scan_cluster(args):
    impl, mode = _impl()
    ns = args.get("namespace")
    want = args.get("severity", "all")

    findings = []
    pods = impl.list_resource("pods", namespace=ns)["items"]
    for p in pods:
        issues = pod_issues(p)
        if issues:
            findings.append({
                "kind": "Pod", "name": (p.get("metadata") or {}).get("name"),
                "namespace": (p.get("metadata") or {}).get("namespace"),
                "node": (p.get("spec") or {}).get("nodeName"),
                "issues": issues,
            })

    if not ns:
        for n in impl.list_resource("nodes")["items"]:
            issues = node_issues(n)
            if issues:
                findings.append({
                    "kind": "Node", "name": (n.get("metadata") or {}).get("name"),
                    "issues": issues,
                })

    for rkey, kind in (("deployments", "Deployment"), ("statefulsets", "StatefulSet"), ("daemonsets", "DaemonSet")):
        for w in impl.list_resource(rkey, namespace=ns)["items"]:
            issues = workload_issues(w)
            if issues:
                findings.append({
                    "kind": kind, "name": (w.get("metadata") or {}).get("name"),
                    "namespace": (w.get("metadata") or {}).get("namespace"),
                    "issues": issues,
                })

    def sev(f):
        return "critical" if any(i["severity"] == "critical" for i in f["issues"]) else "warning"

    if want != "all":
        findings = [f for f in findings if sev(f) == want]
    for f in findings:
        f["severity"] = sev(f)

    # 命中的异常类型同时给出知识库要点，让模型有据可依
    types = sorted({i["type"] for f in findings for i in f["issues"]})
    return {
        "mode": mode,
        "total": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "findings": findings[:60],
        "errorTypes": types,
        "playbook": knowledge.render(types),
    }


def tool_list_resources(args):
    impl, mode = _impl()
    rkey, meta = resolve(args["resource"])
    data = impl.list_resource(rkey, namespace=args.get("namespace"),
                              label_selector=args.get("label_selector"))
    items = [_brief(i) for i in data["items"][:60]]
    return {"mode": mode, "kind": meta["kind"], "count": data["count"], "items": items,
            "truncated": data["count"] > 60}


def tool_get_resource(args):
    impl, mode = _impl()
    obj = impl.get_resource(args["resource"], args["name"], args.get("namespace"))
    obj.get("metadata", {}).pop("managedFields", None)
    return {"mode": mode, "object": obj}


def tool_get_pod_logs(args):
    impl, mode = _impl()
    text = impl.pod_logs(
        args["namespace"], args["name"],
        container=args.get("container"),
        tail=int(args.get("tail", 200)),
        previous=bool(args.get("previous")),
    )
    lines = text.splitlines()
    # 日志常很长，截断保留头尾，兼顾上下文与 token 预算
    if len(lines) > 400:
        text = "\n".join(lines[:200] + [f"... 省略 {len(lines) - 400} 行 ..."] + lines[-200:])
    return {"mode": mode, "logs": text, "lines": len(lines)}


def tool_get_events(args):
    impl, mode = _impl()
    data = impl.list_resource("events", namespace=args.get("namespace"))
    items = data["items"]
    target = args.get("object_name")
    if target:
        items = [e for e in items if (e.get("involvedObject") or {}).get("name") == target]
    items = sorted(items, key=lambda e: e.get("lastTimestamp") or "", reverse=True)
    return {"mode": mode, "count": len(items), "events": [_brief(e) for e in items[:60]]}


def tool_diagnose_object(args):
    impl, mode = _impl()
    rkey, meta = resolve(args["resource"])
    ns = args.get("namespace")
    name = args["name"]

    obj = impl.get_resource(rkey, name, ns)
    kind = meta["kind"]

    if kind == "Pod":
        issues = pod_issues(obj)
    elif kind == "Node":
        issues = node_issues(obj)
    else:
        issues = workload_issues(obj)

    ev_data = impl.list_resource("events", namespace=ns)
    related_events = [
        _brief(e) for e in ev_data["items"]
        if (e.get("involvedObject") or {}).get("name") == name
        or (e.get("involvedObject") or {}).get("name", "").startswith(name + "-")
    ]
    related_events = sorted(related_events, key=lambda e: e.get("lastTimestamp") or "", reverse=True)[:20]

    logs = None
    if kind == "Pod":
        try:
            logs = impl.pod_logs(ns, name, tail=120).splitlines()
            logs = "\n".join(logs[:120])
        except K8sError as exc:
            logs = f"<获取日志失败: {exc.message}>"

        # 若容器正在崩溃，补一次上一次实例的日志
        if any(i["type"] in ("CrashLoopBackOff", "OOMKilled") for i in issues):
            try:
                prev = impl.pod_logs(ns, name, tail=80, previous=True)
                logs += "\n\n--- 上一次崩溃实例的日志 ---\n" + prev
            except K8sError:
                pass

    types = sorted({i["type"] for i in issues})
    return {
        "mode": mode,
        "kind": kind,
        "name": name,
        "namespace": ns,
        "issues": issues,
        "brief": _brief(obj),
        "relatedEvents": related_events,
        "logs": logs,
        "errorTypes": types,
        "playbook": knowledge.render(types),
    }


def tool_get_node_detail(args):
    impl, mode = _impl()
    node = impl.get_resource("nodes", args["name"])
    pods = impl.list_resource("pods", field_selector=f"spec.nodeName={args['name']}")["items"]
    return {
        "mode": mode,
        "node": _brief(node),
        "podCount": len(pods),
        "pods": [{"name": (p.get("metadata") or {}).get("name"),
                  "namespace": (p.get("metadata") or {}).get("namespace"),
                  "phase": (p.get("status") or {}).get("phase")} for p in pods[:80]],
        "allocatable": (node.get("status") or {}).get("allocatable"),
        "taints": (node.get("spec") or {}).get("taints"),
    }


def tool_get_service_endpoints(args):
    impl, mode = _impl()
    ns, name = args["namespace"], args["name"]
    svc = impl.get_resource("services", name, ns)
    selector = (svc.get("spec") or {}).get("selector") or {}

    endpoints = None
    try:
        endpoints = impl.get_resource("endpoints", name, ns)
    except K8sError as exc:
        endpoints = {"error": exc.message}

    pods = impl.list_resource("pods", namespace=ns)["items"]
    matched = [
        {
            "name": (p.get("metadata") or {}).get("name"),
            "labels": (p.get("metadata") or {}).get("labels"),
            "ready": all(c.get("ready") for c in ((p.get("status") or {}).get("containerStatuses") or []))
                     if (p.get("status") or {}).get("containerStatuses") else None,
            "phase": (p.get("status") or {}).get("phase"),
        }
        for p in pods
        if selector and all(((p.get("metadata") or {}).get("labels") or {}).get(k) == v for k, v in selector.items())
    ]

    addresses = ((endpoints or {}).get("subsets") or [])
    ready_count = sum(len(s.get("addresses") or []) for s in addresses)

    return {
        "mode": mode,
        "service": _brief(svc),
        "endpoints": endpoints,
        "readyAddresses": ready_count,
        "selector": selector,
        "matchedPods": matched,
        "conclusion": (
            "Service 没有可用后端：selector 可能匹配不到 Pod，或匹配到的 Pod 未通过就绪探针"
            if ready_count == 0 else f"Service 后端正常，共 {ready_count} 个就绪地址"
        ),
        "playbook": knowledge.render(["ServiceNoEndpoints"] if ready_count == 0 else []),
    }


READONLY_DISPATCH = {
    "get_cluster_overview": tool_get_cluster_overview,
    "scan_cluster": tool_scan_cluster,
    "list_resources": tool_list_resources,
    "get_resource": tool_get_resource,
    "get_pod_logs": tool_get_pod_logs,
    "get_events": tool_get_events,
    "diagnose_object": tool_diagnose_object,
    "get_node_detail": tool_get_node_detail,
    "get_service_endpoints": tool_get_service_endpoints,
}


def run_readonly(name, args, actor="ai"):
    """执行只读工具。"""
    fn = READONLY_DISPATCH.get(name)
    if not fn:
        return {"error": f"未知工具: {name}"}
    try:
        result = fn(args or {})
        audit.record(name, actor=actor, source="ai-agent", success=True,
                     detail={"tool": name, "args": args})
        return result
    except K8sError as exc:
        audit.record(name, actor=actor, source="ai-agent", success=False, message=exc.message)
        return {"error": exc.message, "status": exc.status}
    except Exception as exc:  # noqa: BLE001
        audit.record(name, actor=actor, source="ai-agent", success=False, message=str(exc))
        return {"error": f"工具执行失败: {exc}"}


# ---------------------------------------------------------------------------
# 写操作：生成待确认动作 + 确认后真正执行
# ---------------------------------------------------------------------------
def build_pending_action(name, args):
    """把模型的写操作请求包装成前端可渲染的确认卡片。"""
    desc = {
        "scale_workload": lambda a: f"将 {a.get('namespace')}/{a.get('name')} 的副本数调整为 {a.get('replicas')}",
        "restart_workload": lambda a: f"滚动重启 {a.get('namespace')}/{a.get('name')}",
        "delete_resource": lambda a: f"删除 {a.get('namespace') or ''}/{a.get('name')}（{a.get('resource')}）",
        "cordon_node": lambda a: f"{'禁止调度' if a.get('action') == 'cordon' else '恢复调度'}节点 {a.get('name')}",
        "exec_in_pod": lambda a: f"在 {a.get('namespace')}/{a.get('name')} 执行：{a.get('command')}",
    }.get(name, lambda a: f"执行 {name}: {a}")

    return {
        "id": f"{name}-{abs(hash(str(args))) % 10**8}",
        "tool": name,
        "args": args,
        "description": desc(args or {}),
        "risk": RISK.get(name, "medium"),
        "confirmed": False,
    }


def execute_write_action(tool, args, actor="ai"):
    """用户确认后真正执行写操作。"""
    impl, _ = _impl()
    args = args or {}

    try:
        if tool == "scale_workload":
            impl.scale(args["resource"], args["name"], args.get("namespace"), args["replicas"])
            result = {"scaled": f"{args['name']} -> {args['replicas']} 副本"}
        elif tool == "restart_workload":
            impl.restart(args["resource"], args["name"], args.get("namespace"))
            result = {"restarted": f"{args.get('namespace')}/{args['name']}"}
        elif tool == "delete_resource":
            impl.delete_resource(args["resource"], args["name"], args.get("namespace"))
            result = {"deleted": f"{args.get('namespace') or ''}/{args['name']}"}
        elif tool == "cordon_node":
            impl.node_action(args["name"], args["action"])
            result = {"node": args["name"], "action": args["action"]}
        elif tool == "exec_in_pod":
            cmd = args["command"]
            command = cmd.split() if isinstance(cmd, str) else cmd
            out = impl.pod_exec(args["namespace"], args["name"], command, container=args.get("container"))
            result = {"output": out}
        else:
            raise K8sError(400, f"未知的写操作: {tool}")
    except K8sError as exc:
        audit.record(tool, args.get("resource"), args.get("name"), args.get("namespace"),
                     actor=actor, source="ai-agent", success=False, message=exc.message)
        raise

    audit.record(tool, args.get("resource"), args.get("name"), args.get("namespace"),
                 actor=actor, source="ai-agent", success=True, message=str(result))
    return result


def catalog():
    """给前端展示的工具清单。"""
    out = []
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        out.append({
            "name": fn["name"],
            "description": fn["description"],
            "write": fn["name"] in WRITE_TOOLS,
            "risk": RISK.get(fn["name"], "low"),
        })
    return out


def resource_catalog():
    return [{"key": k, "kind": v["kind"], "label": v["label"], "namespaced": v["namespaced"]}
            for k, v in RESOURCES.items()]
