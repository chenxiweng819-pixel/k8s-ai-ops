"""工作负载与 Pod 的“动作”类接口：扩缩容、重启、回滚、日志、节点维护。"""

from __future__ import annotations

from flask import Blueprint, request

from k8s import ClusterError, cluster, get_spec, object_meta
from .helpers import fail, ok

bp = Blueprint("actions", __name__, url_prefix="/api")


def _payload() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# 工作负载
# ---------------------------------------------------------------------------
@bp.post("/workloads/<res>/<name>/scale")
def scale(res: str, name: str):
    body = _payload()
    try:
        replicas = int(body.get("replicas"))
    except (TypeError, ValueError):
        return fail("replicas 必须是整数", 400)
    if replicas < 0:
        return fail("replicas 不能为负数", 400)

    namespace = body.get("namespace") or request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)

    try:
        obj = cluster.scale(res, name, replicas, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    return ok(obj, f"已将 {name} 副本数调整为 {replicas}")


@bp.post("/workloads/<res>/<name>/restart")
def restart(res: str, name: str):
    body = _payload()
    namespace = body.get("namespace") or request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)
    try:
        obj = cluster.restart(res, name, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    return ok(obj, f"已触发 {name} 滚动重启")


@bp.get("/workloads/<res>/<name>/pods")
def workload_pods(res: str, name: str):
    namespace = request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)
    try:
        pods = cluster.pods_of_workload(res, name, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    return ok(pods)


@bp.get("/workloads/<res>/<name>/rollout")
def rollout(res: str, name: str):
    namespace = request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)
    try:
        return ok(cluster.rollout_status(res, name, namespace))
    except ClusterError as exc:
        return fail(exc.message, exc.status)


# ---------------------------------------------------------------------------
# Pod
# ---------------------------------------------------------------------------
@bp.get("/pods/<name>/logs")
def pod_logs(name: str):
    namespace = request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)
    container = request.args.get("container") or None
    try:
        tail = int(request.args.get("tail") or 200)
    except ValueError:
        tail = 200
    try:
        text = cluster.pod_logs(name, namespace, container, tail)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    return ok(text if text is not None else "")


@bp.get("/pods/<name>/containers")
def pod_containers(name: str):
    namespace = request.args.get("namespace")
    try:
        pod = cluster.get_object("pods", name, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if not pod:
        return fail("未找到该 Pod", 404)
    spec_containers = [c.get("name") for c in ((pod.get("spec") or {}).get("containers") or [])]
    status_containers = [c.get("name") for c in ((pod.get("status") or {}).get("containerStatuses") or [])]
    names = status_containers or spec_containers
    return ok(names)


@bp.post("/pods/<name>/evict")
def pod_evict(name: str):
    """驱逐 Pod（等价 kubectl delete pod，由控制器重建）。"""
    body = _payload()
    namespace = body.get("namespace") or request.args.get("namespace")
    if not namespace:
        return fail("缺少 namespace", 400)
    try:
        deleted = cluster.delete_object("pods", name, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if not deleted:
        return fail("未找到该 Pod", 404)
    return ok({"deleted": True}, f"已驱逐 Pod {name}")


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------
@bp.post("/nodes/<name>/<action>")
def node_action(name: str, action: str):
    if action not in ("cordon", "uncordon"):
        return fail("仅支持 cordon / uncordon", 400)
    try:
        node = cluster.node_action(name, action)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    text = "已标记为不可调度" if action == "cordon" else "已恢复可调度"
    return ok(node, f"节点 {name} {text}")


# ---------------------------------------------------------------------------
# 资源诊断（单资源）
# ---------------------------------------------------------------------------
@bp.get("/resources/<res>/<name>/diagnose")
def resource_diagnose(res: str, name: str):
    """针对单个资源的异常分析：把该资源相关的事件和诊断规则结果聚合起来。"""
    from k8s.diagnose import (
        analyze_nodes,
        analyze_pods,
        analyze_pvcs,
        analyze_workloads,
        diagnose as run_rules,
    )

    namespace = request.args.get("namespace") or None
    spec = get_spec(res)
    if spec is None:
        return fail(f"不支持的资源类型：{res}", 400)

    try:
        obj = cluster.get_object(res, name, namespace)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if obj is None:
        return fail(f"未找到 {res}/{name}", 404)

    # 相关资源：工作负载连带它的 Pod
    related_pods: list[dict] = []
    if spec.name in ("deployments", "statefulsets", "daemonsets") and namespace:
        related_pods = cluster.pods_of_workload(res, name, namespace)

    events = []
    try:
        for ev in cluster.list_objects("events", namespace):
            involved = ev.get("involvedObject") or {}
            if involved.get("name") == name or involved.get("name") in {
                object_meta(p).get("name") for p in related_pods
            }:
                events.append(ev)
    except ClusterError:
        pass

    findings = []
    if spec.name == "pods":
        findings = analyze_pods([obj])
    elif spec.name == "nodes":
        findings = analyze_nodes([obj])
    elif spec.name in ("deployments", "statefulsets", "daemonsets"):
        findings = analyze_workloads([obj], spec.name)
        findings += analyze_pods(related_pods)
    elif spec.name == "persistentvolumeclaims":
        findings = analyze_pvcs([obj])

    # 事件补充
    from k8s.diagnose import analyze_events

    covered = {(f.get("kind"), f.get("namespace"), f.get("name")) for f in findings}
    findings += [
        f
        for f in analyze_events(events)
        if (f.get("kind"), f.get("namespace"), f.get("name")) not in covered
    ]

    # 同时给出整命名空间的体检结果，便于判断是否属于共性问题
    context = None
    try:
        from k8s.collector import collect

        snap = collect(namespace)
        context = run_rules(snap)
    except Exception:  # noqa: BLE001
        context = None

    return ok(
        {
            "resource": {"kind": spec.kind, "name": name, "namespace": namespace or ""},
            "findings": findings,
            "events": events,
            "pods": related_pods,
            "namespaceDiagnosis": context,
        }
    )
