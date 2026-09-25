"""K8s 管理平台后端（默认 8000 端口）。

对外暴露统一的 REST 接口，前端与 5001 的 AI 助手都通过这一层操作集群，
因此「人在界面点的按钮」和「AI 调用的工具」走的是同一套校验、同一套审计。
"""
from __future__ import annotations

import logging
import os
import sys

import yaml
from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import audit, config  # noqa: E402
from backend.diagnose import node_issues, pod_issues, summarize, workload_issues  # noqa: E402
from backend.kube import RESOURCES, SCALABLE, RESTARTABLE, K8sError, get_client, resolve  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("api")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except ImportError:  # 没有 flask-cors 时手工加头，保证前端可跨端口调用
    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp


# ---------------------------------------------------------------------------
# 统一响应与异常
# ---------------------------------------------------------------------------
def ok(data=None, message="ok"):
    return jsonify({"code": 0, "message": message, "data": data})


def fail(message, status=400, **extra):
    payload = {"code": status, "message": message, "data": None}
    payload.update(extra)
    return jsonify(payload), status


@app.errorhandler(K8sError)
def _k8s_error(exc: K8sError):
    return fail(exc.message, exc.status)


@app.errorhandler(Exception)
def _any_error(exc: Exception):
    log.exception("未处理异常")
    return fail(f"服务内部错误: {exc}", 500)


def _svc():
    """获取后端实现（真实集群或演示），返回 (impl, mode)。"""
    return get_client()


def _confirm_required(action, data):
    """危险操作二次确认：未带 confirm 时返回 428，让前端弹窗。"""
    if data.get("confirm") is True:
        return None
    return fail(
        f"「{action}」是危险操作，需要二次确认",
        428,
        needConfirm=True,
        action=action,
        preview=data.get("preview"),
    )


def _actor():
    return request.headers.get("X-Actor", "user")


# ---------------------------------------------------------------------------
# 健康检查 / 集群连接
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    impl, mode = _svc()
    return ok({"status": "ok", "mode": mode, "state": impl.state()})


@app.get("/api/cluster/state")
def cluster_state():
    impl, mode = _svc()
    return ok({"mode": mode, **impl.state()})


@app.post("/api/cluster/kubeconfig")
def upload_kubeconfig():
    """粘贴/上传 kubeconfig 文本，或重新扫描 D:\\config。"""
    impl, mode = _svc()
    data = request.get_json(silent=True) or {}
    text = data.get("kubeconfig")
    action = "import-kubeconfig"

    if mode == "demo":
        # 演示模式下若用户提供了 kubeconfig，直接尝试真实连接
        from backend.kube import KubeClient
        client = KubeClient()
        try:
            if text:
                info = client.save_uploaded_kubeconfig(text)
            else:
                info = client.connect()
        except K8sError as exc:
            audit.record(action, actor=_actor(), success=False, message=exc.message)
            return fail(exc.message, exc.status)
        audit.record(action, actor=_actor(), success=True, message=info.get("context", ""))
        return ok(info)

    try:
        if text:
            info = impl.save_uploaded_kubeconfig(text)
        else:
            info = impl.connect()
    except K8sError as exc:
        audit.record(action, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record(action, actor=_actor(), success=True, message=info.get("context", ""))
    return ok(info)


@app.get("/api/cluster/overview")
def cluster_overview():
    impl, mode = _svc()
    data = impl.cluster_overview()
    data["mode"] = mode
    return ok(data)


@app.get("/api/cluster/registry")
def registry():
    """资源注册表，驱动前端菜单与列定义。"""
    groups = {}
    for key, meta in RESOURCES.items():
        groups.setdefault(meta["group"], []).append(
            {"key": key, "kind": meta["kind"], "label": meta["label"],
             "namespaced": meta["namespaced"],
             "scalable": key in SCALABLE, "restartable": key in RESTARTABLE}
        )
    return ok({"resources": list(RESOURCES.keys()), "groups": groups})


# ---------------------------------------------------------------------------
# 通用资源 CRUD
# ---------------------------------------------------------------------------
@app.get("/api/resources/<res>")
def list_resources(res):
    impl, _ = _svc()
    ns = request.args.get("namespace") or None
    data = impl.list_resource(
        res, namespace=ns,
        label_selector=request.args.get("labelSelector") or None,
        field_selector=request.args.get("fieldSelector") or None,
        limit=request.args.get("limit") or None,
    )
    # 附带异常标记，前端表格直接染色
    kind = data.get("kind")
    if kind in ("Pod", "Node", "Deployment", "StatefulSet", "DaemonSet"):
        issues = summarize(data["items"], kind)
        issue_map = {(i.get("namespace"), i.get("name")): i for i in issues}
        for item in data["items"]:
            m = item.get("metadata") or {}
            hit = issue_map.get((m.get("namespace"), m.get("name")))
            if hit:
                item["_issues"] = hit["issues"]
                item["_severity"] = hit["severity"]
    return ok(data)


@app.get("/api/resources/<res>/<name>")
def get_resource(res, name):
    impl, _ = _svc()
    ns = request.args.get("namespace") or None
    obj = impl.get_resource(res, name, ns)
    _, meta = resolve(res)
    if meta["kind"] == "Pod":
        obj["_issues"] = pod_issues(obj)
    elif meta["kind"] == "Node":
        obj["_issues"] = node_issues(obj)
    elif meta["kind"] in ("Deployment", "StatefulSet", "DaemonSet"):
        obj["_issues"] = workload_issues(obj)
    return ok(obj)


@app.get("/api/resources/<res>/<name>/yaml")
def get_resource_yaml(res, name):
    impl, _ = _svc()
    ns = request.args.get("namespace") or None
    obj = impl.get_resource(res, name, ns)
    # 去掉内部标记，避免污染 YAML
    obj.pop("_issues", None)
    obj.pop("_severity", None)
    meta = obj.get("metadata") or {}
    if meta.get("managedFields"):
        meta.pop("managedFields", None)
    return ok({"yaml": yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=False)})


@app.delete("/api/resources/<res>/<name>")
def delete_resource(res, name):
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    ns = request.args.get("namespace") or data.get("namespace")
    denied = _confirm_required("delete", data)
    if denied:
        return denied

    try:
        impl.delete_resource(res, name, ns)
    except K8sError as exc:
        audit.record("delete", res, name, ns, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record("delete", res, name, ns, actor=_actor(), success=True)
    return ok({"deleted": f"{res}/{name}"})


@app.post("/api/resources/apply")
def apply_yaml():
    """新建或更新资源（kubectl apply 语义）。"""
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    denied = _confirm_required("apply", data)
    if denied:
        return denied

    text = data.get("yaml") or ""
    try:
        manifests = [m for m in yaml.safe_load_all(text) if m]
    except yaml.YAMLError as exc:
        return fail(f"YAML 解析失败: {exc}", 400)
    if not manifests:
        return fail("YAML 内容为空", 400)

    results = []
    for manifest in manifests:
        if not isinstance(manifest, dict):
            continue
        kind = manifest.get("kind")
        name = (manifest.get("metadata") or {}).get("name")
        ns = (manifest.get("metadata") or {}).get("namespace")
        try:
            existing = None
            if name:
                try:
                    existing = impl.get_resource(_key_of_kind(kind), name, ns)
                except K8sError:
                    existing = None
            if existing:
                impl.replace_resource(manifest)
                verb = "updated"
            else:
                impl.create_resource(manifest)
                verb = "created"
            results.append({"kind": kind, "name": name, "namespace": ns, "result": verb})
            audit.record("apply", kind, name, ns, actor=_actor(), success=True, message=verb)
        except K8sError as exc:
            results.append({"kind": kind, "name": name, "namespace": ns, "result": "failed", "error": exc.message})
            audit.record("apply", kind, name, ns, actor=_actor(), success=False, message=exc.message)

    failed = [r for r in results if r["result"] == "failed"]
    if failed:
        return jsonify({"code": 207, "message": "部分资源应用失败", "data": {"results": results}}), 207
    return ok({"results": results})


def _key_of_kind(kind):
    for key, meta in RESOURCES.items():
        if meta["kind"] == kind or meta["kind"].lower() == str(kind).lower():
            return key
    raise K8sError(400, f"不支持的 kind: {kind}")


@app.post("/api/resources/<res>/<name>/scale")
def scale_resource(res, name):
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    ns = data.get("namespace") or request.args.get("namespace")
    replicas = data.get("replicas")

    if replicas is None or not str(replicas).isdigit():
        return fail("replicas 必须是非负整数", 400)
    replicas = int(replicas)
    if replicas > 200:
        return fail("单次副本数不应超过 200", 400)

    denied = _confirm_required("scale", data)
    if denied:
        return denied

    try:
        obj = impl.scale(res, name, ns, replicas)
    except K8sError as exc:
        audit.record("scale", res, name, ns, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record("scale", res, name, ns, actor=_actor(), success=True, message=f"replicas={replicas}")
    return ok(obj)


@app.post("/api/resources/<res>/<name>/restart")
def restart_resource(res, name):
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    ns = data.get("namespace") or request.args.get("namespace")

    denied = _confirm_required("restart", data)
    if denied:
        return denied

    try:
        obj = impl.restart(res, name, ns)
    except K8sError as exc:
        audit.record("restart", res, name, ns, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record("restart", res, name, ns, actor=_actor(), success=True)
    return ok(obj)


# ---------------------------------------------------------------------------
# Pod 相关
# ---------------------------------------------------------------------------
@app.get("/api/pods/<namespace>/<name>/containers")
def pod_containers(namespace, name):
    impl, _ = _svc()
    return ok(impl.pod_containers(namespace, name))


@app.get("/api/pods/<namespace>/<name>/logs")
def pod_logs(namespace, name):
    impl, _ = _svc()
    text = impl.pod_logs(
        namespace, name,
        container=request.args.get("container"),
        tail=int(request.args.get("tail", 200)),
        previous=request.args.get("previous") == "1",
    )
    return ok({"logs": text})


@app.post("/api/pods/<namespace>/<name>/exec")
def pod_exec(namespace, name):
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    denied = _confirm_required("exec", data)
    if denied:
        return denied

    command = data.get("command")
    if isinstance(command, str):
        command = command.split()
    if not command:
        return fail("command 不能为空", 400)

    try:
        out = impl.pod_exec(namespace, name, command, container=data.get("container"))
    except K8sError as exc:
        audit.record("exec", "pods", name, namespace, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record("exec", "pods", name, namespace, actor=_actor(), success=True,
                 message=" ".join(command), detail={"container": data.get("container")})
    return ok({"output": out})


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------
@app.post("/api/nodes/<name>/<action>")
def node_action(name, action):
    impl, _ = _svc()
    data = request.get_json(silent=True) or {}
    if action not in ("cordon", "uncordon"):
        return fail("仅支持 cordon / uncordon", 400)

    denied = _confirm_required(action, data)
    if denied:
        return denied

    try:
        obj = impl.node_action(name, action)
    except K8sError as exc:
        audit.record(action, "nodes", name, None, actor=_actor(), success=False, message=exc.message)
        return fail(exc.message, exc.status)

    audit.record(action, "nodes", name, None, actor=_actor(), success=True)
    return ok(obj)


# ---------------------------------------------------------------------------
# 诊断 / 事件 / 审计
# ---------------------------------------------------------------------------
@app.get("/api/diagnostics")
def diagnostics():
    """全集群异常巡检：节点 / 工作负载 / Pod 三层汇总。

    这个接口是 AI 助手的主要数据源，也是「异常面板」的数据源。
    """
    impl, mode = _svc()
    namespace = request.args.get("namespace") or None

    pods = impl.list_resource("pods", namespace=namespace)["items"]
    nodes = impl.list_resource("nodes")["items"]

    pod_findings = summarize(pods, "Pod")
    node_findings = summarize(nodes, "Node")

    # 注意：list 接口返回的 item 里没有 kind 字段，必须用资源 key 反查 kind，
    # 否则前端会显示成空字符串
    wl_findings = []
    for rkey in ("deployments", "statefulsets", "daemonsets"):
        _, meta = resolve(rkey)
        for w in impl.list_resource(rkey, namespace=namespace)["items"]:
            issues = workload_issues(w)
            if not issues:
                continue
            wl_findings.append({
                "kind": meta["kind"],
                "resource": rkey,
                "namespace": (w.get("metadata") or {}).get("namespace"),
                "name": (w.get("metadata") or {}).get("name"),
                "issues": issues,
                "severity": "critical" if any(i["severity"] == "critical" for i in issues) else "warning",
            })

    all_findings = pod_findings + node_findings + wl_findings
    summary = {
        "total": len(all_findings),
        "critical": sum(1 for f in all_findings if f["severity"] == "critical"),
        "warning": sum(1 for f in all_findings if f["severity"] == "warning"),
        "pods": len(pod_findings),
        "nodes": len(node_findings),
        "workloads": len(wl_findings),
    }

    events = impl.list_resource("events", namespace=namespace)["items"]
    warnings = sorted(
        [e for e in events if e.get("type") == "Warning"],
        key=lambda e: e.get("lastTimestamp") or "",
        reverse=True,
    )[:50]

    return ok({
        "mode": mode,
        "summary": summary,
        "pods": pod_findings,
        "nodes": node_findings,
        "workloads": wl_findings,
        "recentWarnings": warnings,
    })


@app.get("/api/audit")
def audit_log():
    return ok({"items": audit.tail(limit=int(request.args.get("limit", 200)),
                                   actor=request.args.get("actor") or None)})


if __name__ == "__main__":
    log.info("后端启动: http://0.0.0.0:%s", config.BACKEND_PORT)
    app.run(host="0.0.0.0", port=config.BACKEND_PORT, debug=False, threaded=True)
