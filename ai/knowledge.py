"""K8s 排错知识库。

为什么需要它：通用大模型对 K8s 的回答容易停留在「检查日志、检查网络」这类
正确但无用的层面。这里为每种典型异常预置了**判定依据 + 常见根因 + 排查命令 +
修复方案 + 易踩的坑**，在诊断时按命中项注入到提示词中，使 AI 的输出有据可依。

字段说明：
  match        触发的异常类型（与 diagnose.py 的 issue.type 对齐）
  meaning      这个状态到底代表什么
  causes       常见根因（按出现概率排序）
  checks       排查用的 kubectl 命令
  fixes        修复方案
  pitfalls     容易误判或踩坑的地方
  severity     默认严重级别
  docs         相关知识锚点
"""

KNOWLEDGE = {
    "CrashLoopBackOff": {
        "meaning": "容器启动后很快退出，kubelet 按指数退避不断重启它。它本身不是根因，而是「容器进程活不下来」的表象。",
        "causes": [
            "应用自身启动报错（配置缺失、依赖不可达、端口被占用、代码异常）",
            "环境变量或挂载的 ConfigMap/Secret 内容错误，导致进程立刻退出",
            "启动命令/入口脚本错误（command/args 写错、脚本无执行权限、换行符 CRLF）",
            "依赖的下游服务（数据库、Redis、注册中心）未就绪，应用启动即失败",
            "资源 limit 过小导致启动阶段就被 OOMKilled（需结合 lastState 判断）",
        ],
        "checks": [
            "kubectl logs <pod> -n <ns> --previous    # 看上一次崩溃的日志，这是最关键的一步",
            "kubectl describe pod <pod> -n <ns>       # 看 Last State / Exit Code / Events",
            "kubectl get pod <pod> -n <ns> -o jsonpath='{.status.containerStatuses[*].lastState}'",
        ],
        "fixes": [
            "先拿到退出码：137=被 OOM/强杀，1=应用自身错误，126/127=命令不可执行或找不到，143=收到 SIGTERM",
            "应用错误就修配置；依赖未就绪则加 initContainer 等待或加启动重试",
            "确属内存不足就提高 resources.limits.memory，并同步检查 JVM/堆参数",
            "临时定位可用 kubectl run 同镜像 + sleep 覆盖 command，进容器手工跑启动命令",
        ],
        "pitfalls": [
            "只看当前容器日志往往为空，必须加 --previous",
            "把 CrashLoopBackOff 当成「K8s 问题」，实际 99% 是应用或配置问题",
        ],
        "severity": "critical",
        "docs": ["Pod 生命周期", "容器退出码", "资源限制"],
    },
    "ImagePullBackOff": {
        "meaning": "kubelet 无法拉取镜像，正在退避重试。ErrImagePull 是它的第一次失败状态。",
        "causes": [
            "镜像名或 tag 写错（最常见，尤其是不存在的 tag 或漏了 registry 前缀）",
            "私有仓库未配置 imagePullSecrets，或 secret 过期/权限不足",
            "仓库网络不可达（节点无法解析 DNS、无法出网、防火墙拦截）",
            "imagePullPolicy 与 tag 不匹配（tag 用 latest 时默认 Always）",
            "节点磁盘满导致镜像层写入失败",
        ],
        "checks": [
            "kubectl describe pod <pod> -n <ns>    # Events 里会有完整错误，如 not found / unauthorized / timeout",
            "kubectl get secret <name> -n <ns> -o jsonpath='{.data.\\.dockerconfigjson}' | base64 -d",
            "在节点上 docker/nerdctl pull <image> 复现",
        ],
        "fixes": [
            "not found → 修正镜像地址或 tag",
            "unauthorized / 401 → 创建或修正 imagePullSecrets：kubectl create secret docker-registry ...",
            "timeout / no such host → 排查节点 DNS 与出网（先看 /etc/resolv.conf 与 CoreDNS）",
            "disk full → 清理节点镜像：crictl rmi --prune",
        ],
        "pitfalls": [
            "改了镜像地址但 Deployment 未触发滚动更新，需确认 Pod 真的重建了",
            "Secret 里的 .dockerconfigjson 是 base64 后的 JSON，直接手写极易出错",
        ],
        "severity": "critical",
        "docs": ["imagePullSecrets", "私有仓库", "节点 DNS"],
    },
    "Pending": {
        "meaning": "Pod 已创建但调度器找不到合适的节点，或调度成功但容器尚未开始创建。",
        "causes": [
            "节点资源不足（CPU/内存 requests 加起来超过可分配量）",
            "节点亲和性/反亲和性、nodeSelector、taint 无法满足",
            "未绑定的 PVC（WaitForFirstConsumer 的存储类需要先调度再绑盘，会表现为短暂 Pending）",
            "集群节点全部 NotReady 或被 cordon",
            "超出 ResourceQuota 或 LimitRange 限制",
        ],
        "checks": [
            "kubectl describe pod <pod> -n <ns>   # PodScheduled=False 的 message 会直接说明原因",
            "kubectl get nodes -o wide             # 看节点是否 Ready、是否 SchedulingDisabled",
            "kubectl describe node <node>          # 看 Allocated resources 与 Taints",
            "kubectl get pvc -n <ns>               # 看存储是否 Pending",
        ],
        "fixes": [
            "Insufficient cpu/memory → 降 requests、加节点、或扩节点规格",
            "taint 不匹配 → 增加对应 tolerations（注意 master 节点通常有 NoSchedule）",
            "nodeSelector/affinity 写错 → 修正或改成更宽松的匹配",
            "PVC 未就绪 → 先解决存储类与 provisioner 问题",
        ],
        "pitfalls": [
            "Unschedulable 的 message 已经很明确，优先读它而不是猜",
            "临时 Pod 用 sleep 排查时占着资源，别忘删",
        ],
        "severity": "critical",
        "docs": ["调度器", "污点与容忍", "资源配额"],
    },
    "OOMKilled": {
        "meaning": "容器内存使用超过 limit，被内核 cgroup OOM Killer 杀掉（退出码 137）。",
        "causes": [
            "resources.limits.memory 设置过小，达不到应用实际需求",
            "应用内存泄漏，或流量突增导致内存暴涨",
            "JVM 堆未按容器 limit 设置（未加 -XX:MaxRAMPercentage，默认按宿主机内存算）",
            "同一 Pod 多容器共享 limit 时互相挤占（Linux 上 memory limit 是 Pod 级共享）",
        ],
        "checks": [
            "kubectl describe pod <pod> -n <ns>   # Last State: Terminated, Reason: OOMKilled, Exit Code: 137",
            "kubectl top pod <pod> -n <ns>        # 需要 metrics-server",
            "kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources}'",
        ],
        "fixes": [
            "调高 limit 并同步调高 requests（requests 决定调度，limit 决定被杀阈值）",
            "JVM：加 -XX:MaxRAMPercentage=75 -XX:+UseContainerSupport",
            "Node/Python：检查是否有明显泄漏，配置合理的内存上限",
            "稳定后为关键服务配置 HPA 与 PDB，避免单点被打爆",
        ],
        "pitfalls": [
            "只调 limit 不调 requests 会导致调度超卖，节点整体更危险",
            "requests 与 limit 差太大（Burstable）在节点压力大时最先被杀",
        ],
        "severity": "critical",
        "docs": ["服务质量等级 QoS", "cgroup", "退出码 137"],
    },
    "CreateContainerConfigError": {
        "meaning": "kubelet 在创建容器时读不到它引用的 ConfigMap/Secret/Key，容器根本没起来。",
        "causes": [
            "envFrom / env 引用的 ConfigMap 或 Secret 不存在",
            "引用了 ConfigMap 里不存在的 key（optional=false）",
            "Secret/ConfigMap 与 Pod 不在同一命名空间",
            "volume 挂载的 configMap/secret 名字写错",
        ],
        "checks": [
            "kubectl describe pod <pod> -n <ns>   # Events 会写 configmap \"xxx\" not found",
            "kubectl get cm,secret -n <ns>        # 确认对象是否存在",
            "kubectl get pod <pod> -n <ns> -o yaml | grep -A20 envFrom",
        ],
        "fixes": [
            "创建缺失的 ConfigMap/Secret，或修正引用的名字/key",
            "把非必需的引用标记为 optional: true，避免因缺失而阻塞启动",
            "注意命名空间：跨命名空间引用必须在同一 ns 内重建对象",
        ],
        "pitfalls": [
            "ConfigMap 更新后需重建 Pod 才会生效（env 注入不会热更新）",
        ],
        "severity": "critical",
        "docs": ["ConfigMap", "Secret", "环境变量注入"],
    },
    "NotReady": {
        "meaning": "容器进程在跑，但 readinessProbe 未通过，Pod 不会被加入 Service 后端。",
        "causes": [
            "探针路径/端口写错（最常见）",
            "应用启动慢，initialDelaySeconds 太短，探针在启动阶段就开始失败",
            "应用依赖未就绪（数据库连接失败导致健康检查返回非 200）",
            "探针类型不匹配（HTTP 探针对 TCP 端口用错 scheme）",
        ],
        "checks": [
            "kubectl describe pod <pod> -n <ns>    # 看 Readiness probe failed 的详情",
            "kubectl exec -it <pod> -n <ns> -- curl -v localhost:<port>/<path>",
            "kubectl get endpoints <svc> -n <ns>   # 确认是否真的没有后端",
        ],
        "fixes": [
            "修正 probe 的 path/port/scheme",
            "调大 initialDelaySeconds、failureThreshold 或改用 startupProbe",
            "把强依赖改成弱依赖，健康检查只反映自身可用性",
        ],
        "pitfalls": [
            "readiness 失败会导致 Service 无端点，表现为「服务 502」而不是「Pod 异常」，容易查错方向",
        ],
        "severity": "warning",
        "docs": ["探针", "Endpoints", "Service"],
    },
    "HighRestarts": {
        "meaning": "容器重启次数偏高，说明进程偶发崩溃或探针误杀，但还没到 CrashLoopBackOff 的程度。",
        "causes": [
            "livenessProbe 过于激进，把正常但慢的进程杀掉",
            "偶发的依赖超时或网络抖动导致进程退出",
            "内存接近 limit，偶发 OOM",
        ],
        "checks": [
            "kubectl logs <pod> -n <ns> --previous",
            "kubectl describe pod <pod> -n <ns>   # 关注 Liveness probe failed 事件",
        ],
        "fixes": [
            "放宽 livenessProbe 的 timeoutSeconds/failureThreshold",
            "排查依赖稳定性，增加重试与超时控制",
        ],
        "pitfalls": ["重启次数不会自动清零，需以「一段时间内的增量」判断是否仍在恶化"],
        "severity": "warning",
        "docs": ["livenessProbe", "重启策略"],
    },
    "NodeNotReady": {
        "meaning": "节点上的 kubelet 未上报 Ready，其上的 Pod 会被驱逐或标记 Unknown。",
        "causes": [
            "kubelet 或容器运行时（containerd/docker）挂了",
            "节点内存/磁盘压力过大触发驱逐",
            "节点与 API Server 网络不通（证书过期、防火墙、路由问题）",
            "节点磁盘满、inode 耗尽",
        ],
        "checks": [
            "kubectl describe node <node>            # 看 Conditions 与 Events",
            "systemctl status kubelet                # 在节点上执行",
            "journalctl -u kubelet -n 200 --no-pager",
            "df -h && free -m                        # 磁盘与内存",
        ],
        "fixes": [
            "重启 kubelet / containerd：systemctl restart kubelet containerd",
            "清理磁盘：crictl rmi --prune、清理 /var/log",
            "证书过期则续期后重启",
        ],
        "pitfalls": [
            "节点 NotReady 时 Pod 可能显示 Unknown 而不是 Failed，别误判成应用问题",
        ],
        "severity": "critical",
        "docs": ["kubelet", "节点压力驱逐", "证书轮换"],
    },
    "ReplicaUnavailable": {
        "meaning": "工作负载期望的副本数与实际就绪数不一致，滚动更新或扩缩容没有完成。",
        "causes": [
            "底层 Pod 起不来（CrashLoop / ImagePull / Pending）",
            "滚动更新卡在 maxUnavailable，新副本一直不 Ready",
            "探针配置错误导致新副本永远不 Ready",
            "资源配额或节点资源不足，新副本调度不上去",
        ],
        "checks": [
            "kubectl get rs -n <ns> -l <label>        # 看新旧 ReplicaSet 的期望与实际",
            "kubectl describe deploy <name> -n <ns>   # 看 Conditions 与 Events",
            "kubectl rollout status deploy/<name> -n <ns>",
        ],
        "fixes": [
            "先修底层 Pod 的问题，副本数会自然恢复",
            "紧急回滚：kubectl rollout undo deploy/<name> -n <ns>",
            "暂停滚动：kubectl rollout pause，排查后再 resume",
        ],
        "pitfalls": [
            "只看 Deployment 的 AVAILABLE 数字看不出原因，必须下钻到 Pod 与 ReplicaSet",
        ],
        "severity": "critical",
        "docs": ["Deployment 滚动更新", "ReplicaSet", "回滚"],
    },
    "NotAvailable": {
        "meaning": "工作负载的可用副本数少于期望值。READY 看的是「Pod 是否就绪」，AVAILABLE 看的是「已就绪且持续可用超过 minReadySeconds」。因此 AVAILABLE 落后于 READY 时，通常是新副本刚起来还在观察期内，或副本正在反复重启。",
        "causes": [
            "底层 Pod 尚未就绪或正在反复重启，可用副本数上不来",
            "minReadySeconds 设置较长，新副本还在「观察期」内，属正常过渡状态",
            "滚动更新被 maxUnavailable 限制卡住",
            "节点资源不足导致新副本迟迟调度不上去",
        ],
        "checks": [
            "kubectl get deploy <name> -n <ns> -o wide      # 对比 READY 与 AVAILABLE 两列",
            "kubectl get pods -n <ns> -l app=<name> -o wide  # 看 Pod 是否 READY 0/1",
            "kubectl describe deploy <name> -n <ns>          # Conditions 会说明是否卡住",
        ],
        "fixes": [
            "先解决底层 Pod 的 CrashLoop / ImagePull / Pending 问题，AVAILABLE 会自然追上",
            "若只是滚动更新过程，等待 minReadySeconds 后自行消失，无需干预",
            "确需快速恢复：kubectl rollout undo deploy/<name> -n <ns>",
        ],
        "pitfalls": [
            "AVAILABLE 与 READY 不一致不一定是故障，滚动更新中属正常现象，需结合 rollout status 判断",
        ],
        "severity": "warning",
        "docs": ["minReadySeconds", "可用副本", "滚动更新"],
    },
    "ProgressStalled": {        "meaning": "ProgressDeadlineExceeded：Deployment 在 progressDeadlineSeconds 内没有推进，已判定更新失败。",
        "causes": [
            "新版本 Pod 一直起不来（镜像、配置、探针问题）",
            "就绪探针永远不通过",
            "集群资源不足导致新副本无法调度",
        ],
        "checks": [
            "kubectl describe deploy <name> -n <ns>",
            "kubectl get pods -n <ns> -l app=<name>",
        ],
        "fixes": [
            "修复新版本问题，或 kubectl rollout undo 回滚",
            "调大 progressDeadlineSeconds 给慢启动应用更多时间",
        ],
        "pitfalls": ["该状态不会自动恢复，即使后续 Pod 起好了也要等下一次 rollout 才会清除"],
        "severity": "critical",
        "docs": ["progressDeadlineSeconds", "回滚"],
    },
    "PVC": {
        "meaning": "PVC 处于 Pending，说明没有成功绑定到 PV。",
        "causes": [
            "没有匹配的 PV（容量、accessModes、storageClassName 不一致）",
            "storageClassName 拼写错误或该存储类不存在",
            "provisioner 未运行或无法创建后端卷（如 NFS 不可达）",
            "WaitForFirstConsumer 模式下 Pod 还没被调度",
        ],
        "checks": [
            "kubectl describe pvc <pvc> -n <ns>",
            "kubectl get sc                        # 确认存储类与 PROVISIONER",
            "kubectl get pv                        # 看是否有 Available 的 PV",
            "kubectl logs -n <provisioner-ns> <provisioner-pod>",
        ],
        "fixes": [
            "修正 storageClassName / accessModes / 容量使其与 PV 匹配",
            "修复 provisioner（常见于 NFS 地址变更、权限不足）",
            "WaitForFirstConsumer 属正常行为，创建 Pod 后会自动绑定",
        ],
        "pitfalls": [
            "PVC 的 storageClassName 留空会自动用默认存储类，若集群没有默认类就会一直 Pending",
        ],
        "severity": "critical",
        "docs": ["PV/PVC", "StorageClass", "动态供给"],
    },
    "ServiceNoEndpoints": {
        "meaning": "Service 存在但 Endpoints 为空，访问它会直接连接拒绝或 502。",
        "causes": [
            "Service 的 selector 与 Pod 的 labels 不匹配（最常见）",
            "Pod 存在但未 Ready（readiness 探针未通过）",
            "命名空间不一致",
            "targetPort 与容器实际监听端口不一致",
        ],
        "checks": [
            "kubectl get endpoints <svc> -n <ns>",
            "kubectl get pod -n <ns> --show-labels    # 与 Service selector 对比",
            "kubectl get svc <svc> -n <ns> -o yaml | grep -A5 selector",
        ],
        "fixes": [
            "对齐 selector 与 Pod labels",
            "先解决 Pod NotReady 问题",
            "修正 targetPort",
        ],
        "pitfalls": [
            "ClusterIP 能 ping 通不代表正常，ClusterIP 本身不响应 ping；要用 curl 测端口",
        ],
        "severity": "critical",
        "docs": ["Service", "Endpoints", "标签选择器"],
    },
    "DNS": {
        "meaning": "集群内域名解析失败，服务之间互相访问报 no such host。",
        "causes": [
            "CoreDNS Pod 异常或副本不足",
            "CoreDNS ConfigMap 被改坏",
            "Pod 的 dnsPolicy/dnsConfig 配置错误",
            "节点 /etc/resolv.conf 被改坏导致 CoreDNS 转发失败",
        ],
        "checks": [
            "kubectl get pods -n kube-system -l k8s-app=kube-dns",
            "kubectl logs -n kube-system -l k8s-app=kube-dns",
            "kubectl get cm coredns -n kube-system -o yaml",
            "在容器内测试：nslookup kubernetes.default",
        ],
        "fixes": [
            "恢复 CoreDNS 副本与配置（可参考集群备份的 Corefile）",
            "检查上游 DNS 与节点 resolv.conf",
        ],
        "pitfalls": ["改 CoreDNS ConfigMap 后需要重启 CoreDNS Pod 才会生效"],
        "severity": "critical",
        "docs": ["CoreDNS", "Service 发现", "dnsPolicy"],
    },
    "Terminating": {
        "meaning": "Pod 处于 Terminating 状态过久，删除卡住。",
        "causes": [
            "容器进程不响应 SIGTERM 且没有配 terminationGracePeriodSeconds",
            "finalizer 未清理（常见于有挂载卷或 CRD 管理的 Pod）",
            "节点失联，kubelet 无法上报删除完成",
            "volume 卸载卡住（NFS 不可达）",
        ],
        "checks": [
            "kubectl get pod <pod> -n <ns> -o yaml | grep -A10 finalizers",
            "kubectl describe pod <pod> -n <ns>",
        ],
        "fixes": [
            "先等 grace period 结束；仍卡住则强制删除：kubectl delete pod <pod> -n <ns> --grace-period=0 --force",
            "确认应用是否忽略了 SIGTERM（应优雅退出）",
            "节点失联就先解决节点问题",
        ],
        "pitfalls": [
            "--force 删除只是从 etcd 摘除记录，若容器还在跑会变成孤儿容器，需在节点上手工清理",
        ],
        "severity": "warning",
        "docs": ["优雅退出", "finalizer", "强制删除"],
    },
}

# 兜底：当异常类型不在知识库中时给出的通用排查思路
GENERIC_PLAYBOOK = """
通用 K8s 排错顺序（自下而上）：
1. 看事件：kubectl get events -n <ns> --sort-by=.lastTimestamp
2. 看对象详情：kubectl describe <kind> <name> -n <ns>   ← Events 段通常直接给出根因
3. 看日志：kubectl logs <pod> -n <ns> [--previous] [-c <container>]
4. 看资源配置：kubectl get <kind> <name> -n <ns> -o yaml，重点核对 requests/limits、探针、镜像、环境变量
5. 看存储与网络：PVC 是否 Bound、Service 是否有 Endpoints、DNS 是否正常
6. 最后才怀疑集群组件：kubelet / CoreDNS / CNI / 控制器
经验法则：Pod 层面的异常 90% 来自镜像、配置、资源、探针这四类。
"""


def lookup(error_type):
    """按异常类型取知识条目，找不到返回 None。"""
    if not error_type:
        return None
    if error_type in KNOWLEDGE:
        return KNOWLEDGE[error_type]
    # 宽松匹配：忽略大小写，处理 ImagePullBackOff / ErrImagePull 这类同族
    key = str(error_type).lower()
    for name, entry in KNOWLEDGE.items():
        if name.lower() == key:
            return entry
    family = {
        "errimagepull": "ImagePullBackOff",
        "imagepullbackoff": "ImagePullBackOff",
        "crashloopbackoff": "CrashLoopBackOff",
        "unschedulable": "Pending",
        "oomkilling": "OOMKilled",
        "nodeready": "NodeNotReady",
        "notready": "NotReady",
        "progresstalled": "ProgressStalled",
        "progresseadlineexceeded": "ProgressStalled",
        "replicafailure": "ReplicaUnavailable",
        "notavailable": "ReplicaUnavailable",
        "failed": "Pending",
        "failedcreatepodsandbox": "NodeNotReady",
        "evicted": "NodeNotReady",
        "completed": "HighRestarts",
    }
    return KNOWLEDGE.get(family.get(key)) if family.get(key) else None


def render(entries):
    """把命中的知识条目渲染成适合放进提示词的紧凑文本。"""
    if not entries:
        return GENERIC_PLAYBOOK
    chunks = []
    for error_type in entries:
        entry = lookup(error_type)
        if not entry:
            continue
        bullets = lambda items: "".join(f"  - {x}\n" for x in items)
        chunks.append(
            f"### {error_type}\n"
            f"- 含义：{entry['meaning']}\n"
            "- 常见根因：\n" + bullets(entry["causes"]) +
            "- 排查手段：\n" + bullets(entry["checks"]) +
            "- 修复方案：\n" + bullets(entry["fixes"]) +
            "- 易错点：\n" + bullets(entry["pitfalls"])
        )
    return "\n".join(chunks) if chunks else GENERIC_PLAYBOOK
