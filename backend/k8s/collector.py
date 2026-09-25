"""集群快照采集：给诊断引擎和 AI 助手提供统一的、结构化的集群视图。"""

from __future__ import annotations

from .client import ClusterError, cluster


def _safe(res: str, namespace: str | None = None) -> list[dict]:
    try:
        return cluster.list_objects(res, namespace) or []
    except ClusterError:
        return []


def collect(namespace: str | None = None) -> dict:
    """采集一次集群快照。namespace 为空表示全部命名空间。"""
    cluster.load()
    return {
        "pods": _safe("pods", namespace),
        "nodes": _safe("nodes"),
        "namespaces": _safe("namespaces"),
        "deployments": _safe("deployments", namespace),
        "statefulsets": _safe("statefulsets", namespace),
        "daemonsets": _safe("daemonsets", namespace),
        "services": _safe("services", namespace),
        "pvcs": _safe("persistentvolumeclaims", namespace),
        "events": _safe("events", namespace),
    }


def overview(namespace: str | None = None) -> dict:
    """首页概览统计。"""
    snap = collect(namespace)
    pods = snap["pods"]
    nodes = snap["nodes"]

    def _pod_abnormal(pod: dict) -> bool:
        status = pod.get("status") or {}
        if status.get("phase") not in ("Running", "Succeeded"):
            return True
        for cs in status.get("containerStatuses") or []:
            if cs.get("ready") is False:
                return True
            waiting = (cs.get("state") or {}).get("waiting")
            if waiting:
                return True
        return False

    ready_nodes = 0
    for node in nodes:
        for cond in (node.get("status") or {}).get("conditions") or []:
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready_nodes += 1
                break

    totals = {
        "pods": {"total": len(pods), "abnormal": sum(1 for p in pods if _pod_abnormal(p))},
        "nodes": {"total": len(nodes), "ready": ready_nodes},
        "namespaces": {"total": len(snap["namespaces"])},
        "deployments": {"total": len(snap["deployments"])},
        "statefulsets": {"total": len(snap["statefulsets"])},
        "daemonsets": {"total": len(snap["daemonsets"])},
        "services": {"total": len(snap["services"])},
        "pvcs": {"total": len(snap["pvcs"])},
        "warnings": {
            "total": sum(1 for e in snap["events"] if e.get("type") == "Warning")
        },
    }
    return {"totals": totals, "mode": cluster.mode}
