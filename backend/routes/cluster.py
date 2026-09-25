"""集群级别接口：连接状态、概览、命名空间、事件。"""

from __future__ import annotations

from flask import Blueprint, request

from k8s import cluster
from k8s.collector import collect, overview
from k8s.diagnose import diagnose
from .helpers import fail, ok

bp = Blueprint("cluster", __name__, url_prefix="/api/cluster")


@bp.get("/status")
def status():
    cluster.load()
    return ok(cluster.status())


@bp.post("/reload")
def reload_cluster():
    """重新读取 kubeconfig（例如把 config 文件放到 D:\\config 之后）。"""
    return ok(cluster.reload())


@bp.get("/overview")
def get_overview():
    namespace = request.args.get("namespace") or None
    try:
        return ok(overview(namespace))
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@bp.get("/namespaces")
def namespaces():
    try:
        items = cluster.list_objects("namespaces")
        return ok([(i.get("metadata") or {}).get("name") for i in items if (i.get("metadata") or {}).get("name")])
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


@bp.get("/events")
def events():
    namespace = request.args.get("namespace") or None
    only_warning = request.args.get("warning") == "1"
    try:
        items = cluster.list_objects("events", namespace)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    if only_warning:
        items = [e for e in items if e.get("type") == "Warning"]
    items.sort(
        key=lambda e: (e.get("metadata") or {}).get("creationTimestamp") or "",
        reverse=True,
    )
    return ok(items)


@bp.get("/diagnose")
def run_diagnose():
    """整体健康体检：返回异常清单 + 解决方案。"""
    namespace = request.args.get("namespace") or None
    try:
        snap = collect(namespace)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    result = diagnose(snap)
    result["mode"] = cluster.mode
    result["namespace"] = namespace or "全部"
    return ok(result)
