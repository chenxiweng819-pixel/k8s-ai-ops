"""Pod / 工作负载异常判定。

后端 API 与 5001 的 AI 助手共用这一套规则，保证「界面上的红色告警」与
「AI 的诊断结论」永远一致，不会出现界面说异常、AI 说正常的情况。

一条重要经验：**只有「正在发生」的才叫异常**。
容器重启计数是累计值，长期运行的集群（尤其是实验环境反复重启 VM）会累积出
几百次的历史计数，若据此告警会让几乎每个对象都变红，告警就失去意义。
因此重启类告警必须结合「最近一次退出时间」或「当前是否就绪」来判断。
"""
from datetime import datetime, timezone
import time

# 最近多少分钟内发生过容器退出，才认为它在「反复重启」
RECENT_RESTART_MINUTES = 10

# 当前实例运行时间短于此值，且历史重启次数高 → 判定为快速崩溃循环（单次快照即可识别）
SHORT_UPTIME_MINUTES = 2

# 重启计数基线记忆时长：超过这个时长的旧基线不再用于比较
BASELINE_TTL_SECONDS = 1800

# 记录上一次观测到的容器重启次数：key -> (count, 观测时刻)
# 用「计数是否增长」判断抖动，能把「随集群重启只挂一次」和「真的在反复挂」区分开。
_restart_baseline = {}


def _minutes_since(rfc3339):
    """把 K8s 的 RFC3339 时间戳换算成「距今多少分钟」；解析不了返回 None。"""
    if not rfc3339:
        return None
    try:
        dt = datetime.fromisoformat(str(rfc3339).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def _restart_growing(namespace, pod, container, count):
    """判断容器重启次数相对上次观测是否增长。

    返回 True=确认在增长 / False=没增长 / None=缺少可比基线（首次观测或基线过旧）。
    """
    key = (namespace or "", pod or "", container or "")
    now = time.time()
    prev = _restart_baseline.get(key)
    _restart_baseline[key] = (count, now)

    if prev is None:
        return None
    prev_count, prev_at = prev
    if now - prev_at > BASELINE_TTL_SECONDS:
        return None
    return count > prev_count


def _issue(typ, reason, message, severity):
    return {"type": typ, "reason": reason, "message": message, "severity": severity}


def pod_issues(pod):
    """返回 Pod 的异常列表：[{"type","reason","message","severity"}]"""
    issues = []
    status = pod.get("status") or {}
    meta = pod.get("metadata") or {}
    phase = status.get("phase")

    if status.get("reason"):
        issues.append(_issue(status.get("reason"), status.get("message") or "Pod 状态异常", "", "critical"))

    for cs in status.get("containerStatuses") or []:
        name = cs.get("name")
        state = cs.get("state") or {}
        waiting = state.get("waiting") or {}
        terminated = state.get("terminated") or {}
        reason = waiting.get("reason") or terminated.get("reason")
        message = waiting.get("message") or terminated.get("message") or ""
        restarts = cs.get("restartCount") or 0

        if reason == "CrashLoopBackOff":
            issues.append(_issue("CrashLoopBackOff", f"容器 {name} 反复崩溃重启", message, "critical"))
        elif reason in ("ImagePullBackOff", "ErrImagePull"):
            issues.append(_issue(reason, f"容器 {name} 镜像拉取失败", message, "critical"))
        elif reason == "CreateContainerConfigError":
            issues.append(
                _issue(reason, f"容器 {name} 配置引用缺失（常见于 ConfigMap/Secret 不存在）", message, "critical")
            )
        elif reason == "OOMKilled":
            issues.append(_issue("OOMKilled", f"容器 {name} 因内存超限被内核杀掉", message, "critical"))
        elif reason == "Completed" and phase == "Running":
            issues.append(_issue("Completed", f"容器 {name} 已退出但 Pod 仍显示 Running", message, "warning"))
        elif reason and reason not in ("ContainerCreating", "PodInitializing"):
            issues.append(_issue(reason, f"容器 {name} 处于 {reason}", message, "warning"))

        # 重启类告警：必须结合「最近是否真的退过」或「当前是否未就绪」，
        # 否则长期运行集群的历史累计计数会把所有 Pod 都染红（告警失效）
        last_term = (cs.get("lastState") or {}).get("terminated") or {}
        since = _minutes_since(last_term.get("finishedAt"))
        recent_restart = since is not None and since <= RECENT_RESTART_MINUTES

        if restarts >= 3 and not reason and (cs.get("ready") is False or recent_restart):
            extra = (
                f"，最近一次退出在 {max(0, int(since))} 分钟前"
                if recent_restart else "，当前未就绪"
            )
            issues.append(
                _issue("HighRestarts", f"容器 {name} 近期反复重启（累计 {restarts} 次{extra}）", "", "warning")
            )
        if cs.get("ready") is False and not reason and phase == "Running":
            issues.append(_issue("NotReady", f"容器 {name} 未通过就绪探针", "", "warning"))
        if last_term.get("reason") == "OOMKilled":
            issues.append(_issue("OOMKilled", f"容器 {name} 上一次运行因内存超限被杀", "", "critical"))

    if phase == "Pending":
        conds = status.get("conditions") or []
        msg = next(
            (c.get("message") for c in conds if c.get("type") == "PodScheduled" and c.get("status") == "False"),
            "",
        )
        issues.append(_issue("Pending", "Pod 一直处于 Pending，未被调度成功", msg or "", "critical"))
    elif phase == "Failed":
        issues.append(_issue("Failed", "Pod 已失败退出", status.get("message") or "", "critical"))
    elif phase == "Unknown":
        issues.append(_issue("Unknown", "无法获取 Pod 状态（节点失联或 kubelet 异常）", "", "warning"))

    if meta.get("deletionTimestamp"):
        issues.append(_issue("Terminating", "Pod 正在删除且可能已卡住", "", "warning"))

    return issues


def pod_is_abnormal(pod):
    return len(pod_issues(pod)) > 0


def node_issues(node):
    """节点异常判定。"""
    issues = []
    status = node.get("status") or {}
    conds = {c.get("type"): c for c in status.get("conditions") or []}

    for ctype in ("Ready", "MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"):
        cond = conds.get(ctype)
        if not cond:
            continue
        bad = (ctype == "Ready" and cond.get("status") != "True") or (
            ctype != "Ready" and cond.get("status") == "True"
        )
        if bad:
            severity = "critical" if ctype in ("Ready", "MemoryPressure", "DiskPressure") else "warning"
            issue_type = "NodeNotReady" if ctype == "Ready" else ctype
            issues.append(
                _issue(
                    issue_type,
                    f"节点 {ctype} 异常" + (f"：{cond.get('reason')}" if cond.get("reason") else ""),
                    cond.get("message") or "",
                    severity,
                )
            )

    if status.get("spec", {}).get("unschedulable") or node.get("spec", {}).get("unschedulable"):
        issues.append(_issue("Cordoned", "节点已被标记为不可调度（cordon）", "", "warning"))

    if not status.get("nodeInfo", {}).get("kubeletVersion"):
        issues.append(_issue("Unknown", "拿不到 kubelet 版本，节点可能失联", "", "warning"))

    return issues


def workload_issues(obj):
    """Deployment / StatefulSet / DaemonSet 的副本健康度判定。"""
    issues = []
    spec = obj.get("spec") or {}
    status = obj.get("status") or {}
    kind = obj.get("kind")
    name = (obj.get("metadata") or {}).get("name")

    desired = spec.get("replicas")
    if desired is None:
        desired = status.get("desiredNumberScheduled")
    ready = status.get("readyReplicas", status.get("numberReady", 0)) or 0
    available = status.get("availableReplicas", status.get("numberAvailable", ready)) or 0

    if desired is not None and desired > 0:
        if ready < desired:
            issues.append(
                _issue(
                    "ReplicaUnavailable",
                    f"{kind} {name} 期望 {desired} 副本，实际就绪 {ready}",
                    "",
                    "critical" if ready == 0 else "warning",
                )
            )
        if available < desired:
            issues.append(_issue("NotAvailable", f"{kind} {name} 可用副本不足（{available}/{desired}）", "", "warning"))

    for cond in status.get("conditions") or []:
        if cond.get("type") == "Progressing" and cond.get("status") == "False":
            issues.append(_issue("ProgressStalled", f"{kind} {name} 滚动更新停滞", cond.get("message") or "", "critical"))
        if cond.get("type") == "ReplicaFailure" and cond.get("status") == "True":
            issues.append(_issue("ReplicaFailure", f"{kind} {name} 创建副本失败", cond.get("message") or "", "critical"))

    return issues


def summarize(items, kind):
    """按 kind 选择判定函数，返回带问题的对象精简列表。"""
    fn = {
        "Pod": pod_issues,
        "Node": node_issues,
    }.get(kind, workload_issues)

    out = []
    for it in items:
        issues = fn(it)
        if issues:
            out.append(
                {
                    "kind": it.get("kind", kind),
                    "namespace": (it.get("metadata") or {}).get("namespace"),
                    "name": (it.get("metadata") or {}).get("name"),
                    "issues": issues,
                    "severity": "critical" if any(i["severity"] == "critical" for i in issues) else "warning",
                }
            )
    return out
