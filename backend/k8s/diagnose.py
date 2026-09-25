"""异常诊断规则引擎。

从集群快照中找出所有异常，给出：问题定性、根因分析、可执行解决方案、证据。
不依赖大模型也能独立工作；大模型只在上面做归纳、排序和自然语言表达。
"""

from __future__ import annotations

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 知识库：容器/调度/节点 常见异常原因 -> 定性、根因、解决方案
# ---------------------------------------------------------------------------
REASON_KB: dict[str, dict] = {
    "CrashLoopBackOff": {
        "title": "容器反复崩溃重启",
        "severity": "critical",
        "category": "应用运行时",
        "cause": [
            "容器主进程启动后立即退出（退出码非 0），kubelet 按 back-off 策略不断重启",
            "常见诱因：依赖服务不可用（数据库/中间件连不上）、配置项错误、启动脚本报错、代码 panic",
        ],
        "solution": [
            "查看崩溃前日志：`kubectl logs <pod> -n <ns> --previous`，定位第一条 ERROR/PANIC",
            "确认依赖服务可达：检查 Service/Endpoints 与 DNS（`kubectl exec ... -- nslookup <svc>`）",
            "核对 ConfigMap/Secret 是否被正确挂载，环境变量是否缺失",
            "临时手段：`command: [\"sh\",\"-c\",\"sleep 3600\"]` 让容器不退出，进入容器排查",
        ],
    },
    "ImagePullBackOff": {
        "title": "镜像拉取失败",
        "severity": "critical",
        "category": "镜像与仓库",
        "cause": [
            "镜像名称或 tag 写错、镜像在仓库中不存在",
            "私有仓库未配置 imagePullSecrets，或凭据失效",
            "节点无法访问镜像仓库（网络/防火墙/DNS）",
        ],
        "solution": [
            "核对 deployment 中的 image 字段拼写与 tag",
            "创建拉取凭据：`kubectl create secret docker-registry regcred --docker-server=... --docker-username=... --docker-password=...` 并在 spec.imagePullSecrets 引用",
            "在节点上手工验证：`crictl pull <image>` 或 `curl -I https://<registry>/v2/`",
            "检查节点的 containerd 是否配置了 insecure-registries",
        ],
    },
    "ErrImagePull": {
        "title": "镜像拉取出错",
        "severity": "critical",
        "category": "镜像与仓库",
        "cause": ["同 ImagePullBackOff：镜像不存在 / 鉴权失败 / 网络不通"],
        "solution": [
            "查看事件详情确认是 NotFound 还是 Unauthorized/Timeout",
            "NotFound → 修正镜像地址；Unauthorized → 补 imagePullSecrets；Timeout → 排查节点到仓库的网络",
        ],
    },
    "OOMKilled": {
        "title": "容器内存超限被系统杀死",
        "severity": "critical",
        "category": "资源与配额",
        "cause": [
            "容器实际内存使用超过 spec.resources.limits.memory，被 cgroup OOM Killer 终止（退出码 137）",
            "可能存在内存泄漏，或 limit 设置过小、JVM/Go 堆参数未按容器 limit 调整",
        ],
        "solution": [
            "调高内存 limit：`kubectl set resources deploy/<name> -n <ns> --limits=memory=1Gi`",
            "排查内存泄漏：对比 `kubectl top pod` 随时间的增长趋势，抓取 heap profile",
            "为运行时设置容器感知参数（如 JVM 用 `-XX:MaxRAMPercentage=75`）",
            "确认 requests 与 limit 差距合理，避免节点内存超卖",
        ],
    },
    "CreateContainerConfigError": {
        "title": "容器配置错误，无法创建",
        "severity": "high",
        "category": "配置",
        "cause": ["引用的 ConfigMap / Secret 不存在或缺少对应 key"],
        "solution": [
            "确认引用的 ConfigMap/Secret 名称与 namespace 正确",
            "检查 envFrom / valueFrom 里引用的 key 是否真实存在",
            "确认 Secret 与 Pod 在同一 namespace",
        ],
    },
    "CreateContainerError": {
        "title": "容器创建失败",
        "severity": "high",
        "category": "运行时",
        "cause": ["挂载卷失败、entrypoint 不存在、镜像内缺少启动文件"],
        "solution": [
            "检查容器内 command/args 指向的可执行文件是否存在",
            "确认 volume 挂载路径不与只读层冲突",
            "查看节点 kubelet 日志：`journalctl -u kubelet -n 100`",
        ],
    },
    "RunContainerError": {
        "title": "容器启动失败",
        "severity": "high",
        "category": "运行时",
        "cause": ["entrypoint 不存在或不可执行、挂载失败"],
        "solution": ["确认镜像 ENTRYPOINT/CMD 有效", "检查挂载卷与文件权限"],
    },
    "ContainerCreating": {
        "title": "容器长时间处于创建中",
        "severity": "medium",
        "category": "调度与存储",
        "cause": ["镜像拉取慢、PVC 挂载未完成、CNI 网络插件异常"],
        "solution": [
            "查看事件：`kubectl describe pod <pod> -n <ns>`",
            "确认 PVC 是否 Bound，存储插件 Pod 是否正常",
            "确认节点上 calico/flannel 等网络组件 Pod 全部 Running",
        ],
    },
    "Unschedulable": {
        "title": "Pod 无法被调度",
        "severity": "high",
        "category": "调度",
        "cause": [
            "节点资源不足（Insufficient cpu/memory）",
            "节点存在未容忍的污点（untolerated taint）",
            "节点亲和性/反亲和性约束无法满足，或节点被 cordon",
            "PVC 未就绪（WaitForFirstConsumer）导致无法绑定时也会阻塞调度",
        ],
        "solution": [
            "查看调度失败原因：`kubectl describe pod <pod> -n <ns>` 中 PodScheduled 条件",
            "资源不足 → 降低 requests、扩容节点或清理无用 Pod",
            "污点问题 → 添加 tolerations，或对节点 `kubectl uncordon` / 移除 taint",
            "检查 nodeSelector / affinity 是否过于严格",
        ],
    },
    "FailedScheduling": {
        "title": "调度失败",
        "severity": "high",
        "category": "调度",
        "cause": ["资源不足、污点未容忍、亲和性不满足、节点不可用"],
        "solution": [
            "`kubectl describe pod` 查看 scheduler 给出的具体原因",
            "必要时腾出资源：驱逐低优先级 Pod 或扩容节点池",
        ],
    },
    "Evicted": {
        "title": "Pod 被节点驱逐",
        "severity": "high",
        "category": "节点压力",
        "cause": ["节点内存/磁盘压力触发 kubelet 驱逐", "Pod 未设置 requests 导致优先级最低被优先驱逐"],
        "solution": [
            "为 Pod 合理设置 requests/limits，提高 QoS 等级",
            "清理节点磁盘：`crictl rmi --prune`、清理日志",
            "扩容节点内存/磁盘",
        ],
    },
    "NodeNotReady": {
        "title": "节点处于 NotReady",
        "severity": "critical",
        "category": "节点",
        "cause": ["kubelet 停止工作或无法上报心跳", "容器运行时（containerd）异常", "节点负载过高、磁盘写满、网络分区"],
        "solution": [
            "登录节点检查：`systemctl status kubelet containerd`",
            "查看 kubelet 日志：`journalctl -u kubelet -n 200 --no-pager`",
            "检查磁盘与 inode：`df -h`、`df -i`；清理镜像与日志",
            "检查节点到 apiserver 的网络与时间同步",
            "恢复后确认节点 Ready，再 `kubectl uncordon`",
        ],
    },
    "MemoryPressure": {
        "title": "节点内存压力",
        "severity": "high",
        "category": "节点",
        "cause": ["节点可用内存低于阈值，kubelet 开始驱逐 Pod"],
        "solution": ["扩容内存或迁移负载", "降低超卖，检查是否存在内存泄漏 Pod"],
    },
    "DiskPressure": {
        "title": "节点磁盘压力",
        "severity": "high",
        "category": "节点",
        "cause": ["镜像、容器日志或 emptyDir 占满磁盘"],
        "solution": [
            "清理未使用镜像：`crictl rmi --prune`",
            "限制容器日志大小（kubelet 的 containerLogMaxSize）",
            "清理 /var/lib/kubelet 下无用目录",
        ],
    },
    "ProvisioningFailed": {
        "title": "存储卷动态供应失败",
        "severity": "high",
        "category": "存储",
        "cause": ["StorageClass 不存在或 provisioner 未运行", "后端存储不可用、容量不足"],
        "solution": [
            "确认 StorageClass 存在且 provisioner Pod 正常",
            "确认 PVC 的 storageClassName 拼写正确",
            "检查后端存储（NFS/Ceph/云盘）连通性与配额",
        ],
    },
    "Unhealthy": {
        "title": "健康检查失败",
        "severity": "high",
        "category": "应用健康",
        "cause": ["readiness/liveness 探针配置与实际端口或路径不符", "应用启动慢，未留足 initialDelaySeconds"],
        "solution": [
            "核对探针的 port / path / scheme 与应用实际监听是否一致",
            "适当增加 initialDelaySeconds、timeoutSeconds、failureThreshold",
            "确认探针路径不需要鉴权",
        ],
    },
}

# 事件原因 -> 知识库键
EVENT_REASON_MAP = {
    "BackOff": "CrashLoopBackOff",
    "Failed": "ImagePullBackOff",
    "FailedScheduling": "FailedScheduling",
    "OOMKilling": "OOMKilled",
    "NodeNotReady": "NodeNotReady",
    "Evicted": "Evicted",
    "Unhealthy": "Unhealthy",
    "ProvisioningFailed": "ProvisioningFailed",
    "FailedCreatePodSandBox": "CreateContainerError",
    "FailedMount": "CreateContainerConfigError",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now():
    return datetime.now(timezone.utc)


def _age_minutes(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        ts = timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def _finding(reason_key: str, **overrides) -> dict:
    kb = REASON_KB.get(reason_key, {})
    finding = {
        "reason": reason_key,
        "title": kb.get("title", reason_key),
        "severity": kb.get("severity", "medium"),
        "category": kb.get("category", "其他"),
        "cause": list(kb.get("cause", [])),
        "solution": list(kb.get("solution", [])),
        "evidence": [],
        "kind": "",
        "namespace": "",
        "name": "",
    }
    finding.update(overrides)
    return finding


# ---------------------------------------------------------------------------
# Pod 分析
# ---------------------------------------------------------------------------
def _pod_reason(pod: dict) -> tuple[str, str]:
    """返回 (reason_key, 明细信息)。"""
    status = pod.get("status") or {}
    phase = status.get("phase", "")
    statuses = status.get("containerStatuses") or []

    for cs in statuses:
        state = cs.get("state") or {}
        last = cs.get("lastState") or {}
        if "waiting" in state:
            reason = state["waiting"].get("reason", "")
            message = state["waiting"].get("message", "")
            if reason in REASON_KB:
                return reason, message
            if reason:
                return reason, message
        if "terminated" in state:
            term = state["terminated"]
            if term.get("reason") in ("OOMKilled", "Error"):
                return term.get("reason", ""), term.get("message", "")
        if "terminated" in last:
            term = last["terminated"]
            if term.get("reason") == "OOMKilled":
                return "OOMKilled", "上次运行因 OOM 被终止"
            if term.get("exitCode") not in (0, None) and cs.get("restartCount", 0) > 5:
                return "CrashLoopBackOff", f"重启 {cs['restartCount']} 次，上次退出码 {term.get('exitCode')}"

    for cond in status.get("conditions") or []:
        if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
            return "Unschedulable", cond.get("message", "")

    if phase == "Pending":
        return "ContainerCreating", "Pod 处于 Pending"
    if phase == "Failed":
        return "Evicted", status.get("reason", "Pod 已失败")
    return "", ""


def analyze_pods(pods: list[dict]) -> list[dict]:
    findings = []
    for pod in pods:
        meta = pod.get("metadata") or {}
        status = pod.get("status") or {}
        name = meta.get("name", "")
        ns = meta.get("namespace", "")
        phase = status.get("phase", "")
        statuses = status.get("containerStatuses") or []

        reason_key, detail = _pod_reason(pod)

        # 1) 明确的异常原因
        if reason_key:
            evidence = [f"Pod 状态: {phase or 'Unknown'}"]
            if detail:
                evidence.append(f"详情: {detail}")
            total_restarts = sum(cs.get("restartCount", 0) for cs in statuses)
            if total_restarts:
                evidence.append(f"重启次数: {total_restarts}")
            node = (pod.get("spec") or {}).get("nodeName")
            if node:
                evidence.append(f"所在节点: {node}")
            ready = status.get("conditions") or []
            for cond in ready:
                if cond.get("type") == "Ready" and cond.get("status") == "False":
                    evidence.append("Ready 条件为 False")

            finding = _finding(reason_key, kind="Pod", namespace=ns, name=name, evidence=evidence)
            # 未知原因时给出通用建议而不是空方案
            if not finding["solution"]:
                finding["title"] = f"容器异常：{reason_key}"
                finding["cause"] = [detail or "kubelet 报告的容器状态异常"]
                finding["solution"] = [
                    f"查看 Pod 详情的状态与事件：`kubectl describe pod {name} -n {ns}`",
                    f"查看容器日志：`kubectl logs {name} -n {ns} --previous`",
                    "确认镜像、配置、依赖服务与资源限制是否正确",
                ]
            findings.append(finding)
            continue

        # 2) Running 但未就绪
        if phase == "Running":
            not_ready = [cs.get("name") for cs in statuses if cs.get("ready") is False]
            if not_ready:
                findings.append(
                    _finding(
                        "Unhealthy",
                        kind="Pod",
                        namespace=ns,
                        name=name,
                        severity="medium",
                        evidence=[
                            "Pod 处于 Running，但容器未通过就绪检查",
                            f"未就绪容器: {', '.join(not_ready)}",
                        ],
                    )
                )
                continue

        # 3) 重启次数偏高（潜在不稳定）
        total_restarts = sum(cs.get("restartCount", 0) for cs in statuses)
        if total_restarts >= 5:
            findings.append(
                _finding(
                    "CrashLoopBackOff",
                    kind="Pod",
                    namespace=ns,
                    name=name,
                    severity="medium",
                    title="容器频繁重启（尚未进入 CrashLoop）",
                    evidence=[f"累计重启 {total_restarts} 次，当前状态 {phase}"],
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 节点分析
# ---------------------------------------------------------------------------
def analyze_nodes(nodes: list[dict]) -> list[dict]:
    findings = []
    for node in nodes:
        meta = node.get("metadata") or {}
        status = node.get("status") or {}
        spec = node.get("spec") or {}
        name = meta.get("name", "")

        for cond in status.get("conditions") or []:
            ctype = cond.get("type")
            if ctype == "Ready" and cond.get("status") != "True":
                findings.append(
                    _finding(
                        "NodeNotReady",
                        kind="Node",
                        name=name,
                        evidence=[
                            f"Ready={cond.get('status')}, reason={cond.get('reason', '')}",
                            cond.get("message", ""),
                            f"最后心跳: {cond.get('lastHeartbeatTime', '未知')}",
                        ],
                    )
                )
            elif ctype in ("MemoryPressure", "DiskPressure", "PIDPressure") and cond.get("status") == "True":
                findings.append(
                    _finding(
                        ctype,
                        kind="Node",
                        name=name,
                        evidence=[f"{ctype}=True", cond.get("message", "")],
                    )
                )

        if spec.get("unschedulable"):
            findings.append(
                {
                    "reason": "Cordoned",
                    "title": "节点已被标记为不可调度（Cordon）",
                    "severity": "medium",
                    "category": "节点",
                    "kind": "Node",
                    "namespace": "",
                    "name": name,
                    "cause": ["节点被手工 cordon，或正处于排空（drain）流程中"],
                    "solution": [
                        f"确认维护完成后恢复调度：`kubectl uncordon {name}`",
                        "若为 drain 过程中，等待维护结束再 uncordon",
                    ],
                    "evidence": ["spec.unschedulable=true"],
                }
            )
    return findings


# ---------------------------------------------------------------------------
# 工作负载分析
# ---------------------------------------------------------------------------
def analyze_workloads(workloads: list[dict], res_name: str) -> list[dict]:
    findings = []
    for item in workloads:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        name = meta.get("name", "")
        ns = meta.get("namespace", "")

        desired = spec.get("replicas", 0)
        ready = status.get("readyReplicas", 0) or 0
        available = status.get("availableReplicas", 0) or 0

        if desired and ready < desired:
            findings.append(
                {
                    "reason": "ReplicasUnavailable",
                    "title": f"副本数不足（{ready}/{desired} 就绪）",
                    "severity": "high" if ready == 0 else "medium",
                    "category": "工作负载",
                    "kind": res_name,
                    "namespace": ns,
                    "name": name,
                    "cause": [
                        "底层 Pod 未能全部进入 Ready 状态",
                        "常见原因：镜像拉取失败、容器崩溃、就绪探针失败、资源不足",
                    ],
                    "solution": [
                        f"查看该工作负载下的 Pod：`kubectl get pods -n {ns} -l app={meta.get('labels', {}).get('app', name)}`",
                        f"查看事件与滚动历史：`kubectl describe {res_name[:-1]} {name} -n {ns}`",
                        f"如需回滚：`kubectl rollout undo {res_name[:-1]}/{name} -n {ns}`",
                    ],
                    "evidence": [
                        f"期望副本 {desired}，就绪 {ready}，可用 {available}",
                        f"unavailableReplicas={status.get('unavailableReplicas', desired - ready)}",
                    ],
                }
            )
    return findings


# ---------------------------------------------------------------------------
# 存储分析
# ---------------------------------------------------------------------------
def analyze_pvcs(pvcs: list[dict]) -> list[dict]:
    findings = []
    for pvc in pvcs:
        meta = pvc.get("metadata") or {}
        status = pvc.get("status") or {}
        phase = status.get("phase", "")
        if phase in ("Pending", "Lost"):
            key = "ProvisioningFailed" if phase == "Pending" else "Evicted"
            findings.append(
                _finding(
                    key if key in REASON_KB else "ProvisioningFailed",
                    kind="PersistentVolumeClaim",
                    namespace=meta.get("namespace", ""),
                    name=meta.get("name", ""),
                    title="存储声明未绑定" if phase == "Pending" else "存储声明已丢失",
                    evidence=[f"PVC 状态: {phase}"],
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 事件分析（补充：把 Warning 事件也纳入，即使对应资源已消失）
# ---------------------------------------------------------------------------
def analyze_events(events: list[dict]) -> list[dict]:
    findings = []
    for ev in events:
        if ev.get("type") != "Warning":
            continue
        reason = ev.get("reason", "")
        key = EVENT_REASON_MAP.get(reason)
        obj = ev.get("involvedObject") or {}
        if not key:
            continue
        # 这类事件已经由资源级规则覆盖，避免重复；只保留资源已不存在的
        findings.append(
            _finding(
                key,
                kind=obj.get("kind", ""),
                namespace=obj.get("namespace", "") or "",
                name=obj.get("name", ""),
                evidence=[
                    f"事件原因: {reason}",
                    f"事件消息: {ev.get('message', '')}",
                    f"出现次数: {ev.get('count', 1)}",
                ],
                fromEvent=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------
def diagnose(snapshot: dict) -> dict:
    """snapshot 由 collector 提供：pods/nodes/deployments/statefulsets/daemonsets/pvcs/events"""
    findings: list[dict] = []

    findings += analyze_pods(snapshot.get("pods", []))
    findings += analyze_nodes(snapshot.get("nodes", []))
    findings += analyze_workloads(snapshot.get("deployments", []), "deployments")
    findings += analyze_workloads(snapshot.get("statefulsets", []), "statefulsets")
    findings += analyze_workloads(snapshot.get("daemonsets", []), "daemonsets")
    findings += analyze_pvcs(snapshot.get("pvcs", []))

    # 事件作为兜底：只保留没有对应资源级发现的
    covered = {(f.get("kind"), f.get("namespace"), f.get("name")) for f in findings}
    for ev_finding in analyze_events(snapshot.get("events", [])):
        sig = (ev_finding.get("kind"), ev_finding.get("namespace"), ev_finding.get("name"))
        if sig not in covered:
            findings.append(ev_finding)

    # 严重度排序
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity", "medium"), 9))

    summary = {
        "total": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
        "low": sum(1 for f in findings if f["severity"] == "low"),
        "byCategory": {},
    }
    for f in findings:
        cat = f.get("category", "其他")
        summary["byCategory"][cat] = summary["byCategory"].get(cat, 0) + 1

    return {
        "summary": summary,
        "findings": findings,
        "healthScore": _health_score(findings, snapshot),
    }


def _health_score(findings: list[dict], snapshot: dict) -> int:
    """0-100 的粗略健康分，用于首页展示。"""
    score = 100
    weights = {"critical": 12, "high": 6, "medium": 2, "low": 1}
    for f in findings:
        score -= weights.get(f.get("severity", "medium"), 2)
    pods = snapshot.get("pods", [])
    if pods:
        abnormal = len({(f.get("namespace"), f.get("name")) for f in findings if f.get("kind") == "Pod"})
        score -= int(abnormal / max(len(pods), 1) * 10)
    return max(0, min(100, score))
