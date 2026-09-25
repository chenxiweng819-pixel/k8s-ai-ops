"""K8s 专用 AI 助手服务（默认 5001 端口）。

它同时扮演三个角色：
1. **对话助手**：结合集群实时数据回答运维问题；
2. **排错专家**：分析异常状态、给出根因与修复方案；
3. **操作代理**：可以发起对平台/集群的操作，但危险动作必须经用户确认。

与后端（8000）的关系：AI 直接复用 backend 的 service 层与审计模块，
因此「AI 做的操作」和「界面点的按钮」经同一条路径，审计记录也写在同一处。
"""
from __future__ import annotations

import logging
import os
import sys

# --- 先加载 .env，再导入其余模块（配置在 import 期读取环境变量）---
from dotenv import load_dotenv

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

for candidate in (
    os.path.join(PROJECT_DIR, ".env"),
    os.path.join(os.path.expanduser("~"), ".env"),
):
    if os.path.isfile(candidate):
        load_dotenv(candidate, override=False)

from flask import Flask, jsonify, request  # noqa: E402

from ai import agent, knowledge, tools  # noqa: E402
from backend import audit, config  # noqa: E402
from backend.kube import get_client  # noqa: E402
from backend.registry import K8sError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ai")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

try:
    from flask_cors import CORS
    CORS(app, resources={r"/*": {"origins": "*"}})
except ImportError:
    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return resp

# 对话历史保存在内存中，按会话隔离（重启即清空，够用且无隐私落盘问题）
_SESSIONS: dict[str, list] = {}
MAX_HISTORY = 20


def ok(data=None, message="ok"):
    return jsonify({"code": 0, "message": message, "data": data})


def fail(message, status=400):
    return jsonify({"code": status, "message": message, "data": None}), status


@app.errorhandler(K8sError)
def _k8s(exc: K8sError):
    return fail(exc.message, exc.status)


@app.errorhandler(Exception)
def _any(exc: Exception):
    log.exception("AI 服务未处理异常")
    return fail(f"AI 服务内部错误: {exc}", 500)


def _actor():
    return request.headers.get("X-Actor", "ai-user")


@app.get("/health")
def health():
    impl, mode = get_client()
    return ok({
        "status": "ok",
        "service": "k8s-ai-assistant",
        "port": config.AI_PORT,
        "clusterMode": mode,
        "llmConfigured": bool(config.LLM_API_KEY),
        "llmModel": config.LLM_MODEL if config.LLM_API_KEY else None,
        "state": impl.state(),
    })


@app.get("/api/tools")
def list_tools():
    """列出 AI 可用的工具，前端「能力说明」面板使用。"""
    return ok({"tools": tools.catalog(), "resources": tools.resource_catalog()})


@app.get("/api/knowledge")
def get_knowledge():
    """排错知识库。

    前端「异常巡检」页直接用它渲染「常见根因 / 排查命令 / 修复方案」，
    与 AI 诊断用的是同一份知识，避免界面与 AI 给出不一致的结论。
    """
    return ok({"knowledge": knowledge.KNOWLEDGE, "generic": knowledge.GENERIC_PLAYBOOK})


@app.get("/api/snapshot")
def snapshot():
    """当前集群快照（前端可展示 AI 眼中的集群状态）。"""
    return ok(agent.cluster_snapshot(request.args.get("namespace") or None))


@app.post("/api/chat")
def chat():
    """主对话入口。

    请求体：
      message    必填，用户问题
      sessionId  可选，用于隔离多轮上下文
      namespace  可选，限定分析范围
      focus      可选，{kind,name,namespace} 当前关注对象
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return fail("message 不能为空", 400)
    if len(message) > 8000:
        return fail("问题过长，请精简到 8000 字符以内", 400)

    session_id = data.get("sessionId") or "default"
    history = _SESSIONS.get(session_id, [])

    result = agent.run(
        message,
        history=history,
        namespace=data.get("namespace") or None,
        focus=data.get("focus") or None,
        actor=_actor(),
    )

    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": result.get("answer", "")[:6000]},
    ]
    _SESSIONS[session_id] = history[-MAX_HISTORY * 2:]

    return ok(result)


@app.post("/api/analyze")
def analyze():
    """一键诊断：针对单个资源给出问题定性与解决方案。"""
    data = request.get_json(silent=True) or {}
    resource = data.get("resource")
    name = data.get("name")
    if not resource or not name:
        return fail("需要 resource 与 name", 400)

    result = agent.analyze_object(resource, name, data.get("namespace"), actor=_actor())
    return ok(result)


@app.post("/api/execute")
def execute():
    """执行 AI 提议的写操作（必须显式 confirm=true）。"""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return fail("该操作需要二次确认，请传 confirm=true", 428)

    tool = data.get("tool")
    args = data.get("args") or {}
    if tool not in tools.WRITE_TOOLS:
        return fail(f"不支持的写操作: {tool}", 400)

    try:
        result = tools.execute_write_action(tool, args, actor=_actor())
    except K8sError as exc:
        return fail(exc.message, exc.status)

    return ok({
        "result": result,
        "description": tools.build_pending_action(tool, args)["description"],
    })


@app.get("/api/diagnostics")
def diagnostics():
    """AI 视角的集群巡检（规则引擎结果，不消耗模型额度）。"""
    return ok(tools.tool_scan_cluster({
        "namespace": request.args.get("namespace") or None,
        "severity": request.args.get("severity") or "all",
    }))


@app.get("/api/audit")
def audit_log():
    return ok({"items": audit.tail(limit=int(request.args.get("limit", 200)))})


@app.post("/api/session/reset")
def reset_session():
    data = request.get_json(silent=True) or {}
    _SESSIONS.pop(data.get("sessionId") or "default", None)
    return ok({"reset": True})


if __name__ == "__main__":
    log.info("AI 助手启动: http://0.0.0.0:%s (model=%s, configured=%s)",
             config.AI_PORT, config.LLM_MODEL, bool(config.LLM_API_KEY))
    app.run(host="0.0.0.0", port=config.AI_PORT, debug=False, threaded=True)
