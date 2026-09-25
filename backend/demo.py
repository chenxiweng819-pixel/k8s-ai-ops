"""内置演示集群。

当 kubeconfig 缺失或集群连不上时，后端会退回这个内存实现。它的方法与
KubeClient 完全一致，因此上层路由、AI 工具链都无需改动，平台仍然可以
完整演示「列表 / 详情 / YAML / 扩缩容 / 重启 / 删除 / 日志 / 诊断」。

数据刻意包含 CrashLoopBackOff、ImagePullBackOff、Pending、OOMKilled 等
典型故障，方便验证 AI 助手的排错能力。
"""
from __future__ import annotations

import copy
import time
import uuid

from .diagnose import node_issues, pod_issues, summarize
from .registry import RESOURCES, RESTARTABLE, SCALABLE, K8sError, resolve

NS = ["default", "kube-system", "dev", "prod", "monitoring"]


def _meta(name, namespace=None, labels=None, extra=None):
    m = {"name": name, "uid": str(uuid.uuid4()), "creationTimestamp": "2026-08-01T08:00:00Z"}
    if namespace:
        m["namespace"] = namespace
    if labels:
        m["labels"] = labels
    if extra:
        m.update(extra)
    return m


def _pod(name, namespace, node, phase, containers, **kw):
    cstats = []
    for c in containers:
        cstats.append(
            {
                "name": c["name"],
                "image": c["image"],
                "ready": c.get("ready", phase == "Running"),
                "restartCount": c.get("restarts", 0),
                "state": c.get("state", {"running": {"startedAt": "2026-08-01T08:00:00Z"}}),
                "lastState": c.get("lastState", {}),
            }
        )
    conds = kw.pop("conditions", [{"type": "PodScheduled", "status": "True"}, {"type": "Ready", "status": "True"}])
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": _meta(name, namespace, kw.pop("labels", {"app": name.rsplit("-", 2)[0]}), kw.pop("metaExtra", None)),
        "spec": {
            "nodeName": node,
            "containers": [{"name": c["name"], "image": c["image"]} for c in containers],
            **({"nodeSelector": {"disktype": "ssd"}} if phase == "Pending" else {}),
        },
        "status": {
            "phase": phase,
            "podIP": f"10.244.{abs(hash(name)) % 250}.{abs(hash(namespace)) % 250}",
            "startTime": "2026-08-01T08:00:05Z",
            "containerStatuses": cstats,
            "conditions": conds,
            **kw,
        },
    }


def build_demo_state():
    """构造一份完整的演示集群快照。"""
    nodes = []
    for i, (name, ready) in enumerate(
        [("cka-master", True), ("cka-node1", True), ("cka-node2", False)], start=1
    ):
        conds = [
            {"type": "Ready", "status": "True" if ready else "False",
             "reason": "KubeletReady" if ready else "KubeletNotReady",
             "message": "kubelet is posting ready status" if ready else "container runtime is down"},
            {"type": "MemoryPressure", "status": "False"},
            {"type": "DiskPressure", "status": "False"},
        ]
        nodes.append(
            {
                "apiVersion": "v1",
                "kind": "Node",
                "metadata": _meta(name, labels={"kubernetes.io/hostname": name}, extra={"annotations": {}}),
                "spec": {"unschedulable": False},
                "status": {
                    "conditions": conds,
                    "nodeInfo": {"kubeletVersion": "v1.29.4", "osImage": "Rocky Linux 9.4", "containerRuntimeVersion": "containerd://1.7.13"},
                    "capacity": {"cpu": "8", "memory": "16Gi", "pods": "110"},
                    "allocatable": {"cpu": "7800m", "memory": "15Gi", "pods": "110"},
                    "addresses": [{"type": "InternalIP", "address": f"192.168.234.{10 + i}"}],
                },
            }
        )

    namespaces = []
    for ns in NS:
        namespaces.append(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": _meta(ns, extra={"labels": {"kubernetes.io/metadata.name": ns}}),
                "status": {"phase": "Active"},
            }
        )

    pods = [
        # 正常
        _pod("nginx-6b8f7c9d4f-abcde", "default", "cka-node1", "Running",
             [{"name": "nginx", "image": "nginx:1.25", "restarts": 0}]),
        _pod("redis-0", "default", "cka-node1", "Running",
             [{"name": "redis", "image": "redis:7.2", "restarts": 0}]),
        # CrashLoopBackOff
        _pod("api-server-7d4b9c8f5-xyz12", "prod", "cka-node1", "Running",
             [{"name": "api", "image": "registry.local/api:v2.3.1", "restarts": 14, "ready": False,
               "state": {"waiting": {"reason": "CrashLoopBackOff",
                                     "message": "back-off 5m0s restarting failed container=api pod=api-server-7d4b9c8f5-xyz12_prod"}},
               "lastState": {"terminated": {"reason": "Error", "exitCode": 1, "message": "panic: cannot connect to database"}}}],
             labels={"app": "api-server"}),
        # ImagePullBackOff
        _pod("worker-5f7d8b6c9-k9lm3", "dev", "cka-node2", "Pending",
             [{"name": "worker", "image": "registry.local/worker:v9.9.9", "restarts": 0, "ready": False,
               "state": {"waiting": {"reason": "ImagePullBackOff",
                                     "message": "Back-off pulling image \"registry.local/worker:v9.9.9\": not found"}}}]),
        # 真·Pending（调度失败）
        _pod("batch-job-8c9d7e6f5-pq45r", "dev", None, "Pending",
             [{"name": "batch", "image": "busybox:1.36"}],
             conditions=[{"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                          "message": "0/3 nodes are available: 1 node(s) had untolerated taint, 2 Insufficient cpu."}]),
        # OOMKilled
        _pod("cache-warmer-3a4b5c6d7-mn89o", "prod", "cka-node1", "Running",
             [{"name": "warmer", "image": "registry.local/warmer:1.0", "restarts": 6, "ready": True,
               "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137, "message": "container exceeded memory limit"}}}]),
        # CreateContainerConfigError
        _pod("web-4d5e6f7a8-stu01", "dev", "cka-node1", "Pending",
             [{"name": "web", "image": "registry.local/web:1.4", "ready": False,
               "state": {"waiting": {"reason": "CreateContainerConfigError",
                                     "message": "configmap \"web-config\" not found"}}}]),
        # 系统组件
        _pod("coredns-5dd5756b68-hhh66", "kube-system", "cka-master", "Running",
             [{"name": "coredns", "image": "registry.k8s.io/coredns/coredns:v1.11.1", "restarts": 0}]),
        _pod("metrics-server-7cd6b8f9c-tt22p", "kube-system", "cka-node1", "Running",
             [{"name": "metrics-server", "image": "registry.k8s.io/metrics-server/metrics-server:v0.7.1", "restarts": 1}]),
        _pod("prometheus-0", "monitoring", "cka-node1", "Running",
             [{"name": "prometheus", "image": "prom/prometheus:v2.51.0", "restarts": 0}]),
    ]

    deployments = [
        _d("nginx", "default", 2, 2, "Running"),
        _d("api-server", "prod", 3, 1, "CrashLoop-ish"),
        _d("web", "dev", 2, 0, "Pending"),
        _d("worker", "dev", 1, 0, "ImagePull"),
        _d("cache-warmer", "prod", 2, 2, "Running"),
    ]

    statefulsets = [_sts("redis", "default", 1, 1), _sts("prometheus", "monitoring", 1, 1)]
    daemonsets = [_ds("kube-proxy", "kube-system", 3, 2), _ds("filebeat", "monitoring", 3, 3)]

    services = [
        _svc("kubernetes", "default", "ClusterIP", "10.96.0.1", 443),
        _svc("nginx", "default", "NodePort", "10.96.10.20", 80, node_port=31080),
        _svc("api-server", "prod", "ClusterIP", "10.96.20.30", 8080),
        _svc("redis", "default", "ClusterIP", "10.96.10.50", 6379, headless=True),
        _svc("prometheus", "monitoring", "NodePort", "10.96.30.10", 9090, node_port=30090),
    ]

    ingresses = [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": _meta("web-ingress", "prod", extra={"annotations": {"nginx.ingress.kubernetes.io/rewrite-target": "/"}}),
            "spec": {
                "ingressClassName": "nginx",
                "rules": [{"host": "api.example.com", "http": {"paths": [
                    {"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "api-server", "port": {"number": 8080}}}}
                ]}}],
                "tls": [{"hosts": ["api.example.com"], "secretName": "api-tls"}],
            },
            "status": {"loadBalancer": {"ingress": [{"ip": "192.168.234.10"}]}},
        }
    ]

    configmaps = [
        _cm("web-config", "dev", {"APP_ENV": "dev", "LOG_LEVEL": "debug"}),
        _cm("nginx-conf", "default", {"nginx.conf": "server {\n  listen 80;\n}\n"}),
        _cm("coredns", "kube-system", {"Corefile": ".:53 {\n    forward . /etc/resolv.conf\n}\n"}),
    ]

    secrets = [
        _secret("api-tls", "prod", {"tls.crt": "LS0tLS1CRUdJTi...", "tls.key": "LS0tLS1CRUdJTi..."}),
        _secret("db-credentials", "prod", {"username": "YWRtaW4=", "password": "U0VDUkVUX1BBU1M="}),
        _secret("regcred", "default", {".dockerconfigjson": "eyJhdXRocyI6e319"}),
    ]

    pvcs = [
        _pvc("redis-data-redis-0", "default", "10Gi", "Bound", "standard"),
        _pvc("prometheus-data", "monitoring", "50Gi", "Bound", "standard"),
        _pvc("batch-data", "dev", "5Gi", "Pending", "nfs-client"),
    ]

    pvs = [
        _pv("pvc-redis", "10Gi", "Bound", "standard"),
        _pv("pvc-prom", "50Gi", "Bound", "standard"),
        _pv("pvc-orphan", "20Gi", "Available", "nfs-client"),
    ]

    storageclasses = [
        {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
         "metadata": _meta("standard", extra={"annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}),
         "provisioner": "kubernetes.io/local-path", "reclaimPolicy": "Delete", "volumeBindingMode": "WaitForFirstConsumer"},
        {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
         "metadata": _meta("nfs-client"),
         "provisioner": "cluster.local/nfs-subdir-external-provisioner", "reclaimPolicy": "Retain", "volumeBindingMode": "Immediate"},
    ]

    events = [
        _ev("api-server-7d4b9c8f5-xyz12", "prod", "Warning", "BackOff",
            "Back-off restarting failed container api in pod api-server-7d4b9c8f5-xyz12_prod", 12),
        _ev("worker-5f7d8b6c9-k9lm3", "dev", "Warning", "Failed",
            "Failed to pull image \"registry.local/worker:v9.9.9\": not found", 8),
        _ev("batch-job-8c9d7e6f5-pq45r", "dev", "Warning", "FailedScheduling",
            "0/3 nodes are available: 2 Insufficient cpu, 1 node(s) had untolerated taint", 5),
        _ev("cache-warmer-3a4b5c6d7-mn89o", "prod", "Warning", "OOMKilling",
            "Memory cgroup out of memory: Killed process", 22),
        _ev("cka-node2", None, "Warning", "NodeNotReady",
            "Node cka-node2 status is now: NodeNotReady", 1),
        _ev("web-4d5e6f7a8-stu01", "dev", "Warning", "Failed",
            "Error: configmap \"web-config\" not found", 6),
        _ev("nginx-6b8f7c9d4f-abcde", "default", "Normal", "Scheduled", "Successfully assigned default/nginx to cka-node1", 30),
    ]

    return {
        "nodes": nodes, "namespaces": namespaces, "pods": pods,
        "deployments": deployments, "statefulsets": statefulsets, "daemonsets": daemonsets,
        "replicasets": [], "jobs": [], "cronjobs": [], "horizontalpodautoscalers": [],
        "services": services, "endpoints": [], "ingresses": ingresses, "networkpolicies": [],
        "configmaps": configmaps, "secrets": secrets, "serviceaccounts": [],
        "persistentvolumeclaims": pvcs, "persistentvolumes": pvs, "storageclasses": storageclasses,
        "events": events, "roles": [], "rolebindings": [], "clusterroles": [], "clusterrolebindings": [],
    }


# --- 构造工具 ---
def _d(name, ns, desired, ready, state):
    status = {"replicas": ready, "readyReplicas": ready, "availableReplicas": ready,
              "updatedReplicas": ready, "observedGeneration": 1}
    if state == "CrashLoop-ish":
        status["conditions"] = [{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded",
                                 "message": 'ReplicaSet "api-server-7d4b9c8f5" has timed out progressing.'}]
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": _meta(name, ns, {"app": name}, {"annotations": {"deployment.kubernetes.io/revision": "3"}}),
        "spec": {"replicas": desired,
                 "selector": {"matchLabels": {"app": name}},
                 "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}},
                 "template": {"metadata": {"labels": {"app": name}},
                              "spec": {"containers": [{"name": name, "image": f"registry.local/{name}:latest",
                                                       "resources": {"limits": {"cpu": "500m", "memory": "512Mi"}}}]}}},
        "status": status,
    }


def _sts(name, ns, desired, ready):
    return {
        "apiVersion": "apps/v1", "kind": "StatefulSet",
        "metadata": _meta(name, ns, {"app": name}),
        "spec": {"replicas": desired, "serviceName": name,
                 "selector": {"matchLabels": {"app": name}},
                 "template": {"metadata": {"labels": {"app": name}},
                              "spec": {"containers": [{"name": name, "image": f"registry.local/{name}:latest"}]}}},
        "status": {"replicas": ready, "readyReplicas": ready, "availableReplicas": ready, "currentReplicas": ready},
    }


def _ds(name, ns, desired, ready):
    return {
        "apiVersion": "apps/v1", "kind": "DaemonSet",
        "metadata": _meta(name, ns, {"app": name}),
        "spec": {"selector": {"matchLabels": {"app": name}},
                 "template": {"metadata": {"labels": {"app": name}},
                              "spec": {"containers": [{"name": name, "image": f"registry.local/{name}:latest"}]}}},
        "status": {"desiredNumberScheduled": desired, "numberReady": ready,
                   "numberAvailable": ready, "currentNumberScheduled": desired},
    }


def _svc(name, ns, typ, cluster_ip, port, node_port=None, headless=False):
    spec = {"type": typ, "clusterIP": "None" if headless else cluster_ip,
            "ports": [{"name": "http", "port": port, "targetPort": port, "protocol": "TCP"}]}
    if node_port:
        spec["ports"][0]["nodePort"] = node_port
    if not headless:
        spec["selector"] = {"app": name}
    return {"apiVersion": "v1", "kind": "Service", "metadata": _meta(name, ns, {"app": name}), "spec": spec,
            "status": {"loadBalancer": {}}}


def _cm(name, ns, data):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": _meta(name, ns), "data": data}


def _secret(name, ns, data):
    return {"apiVersion": "v1", "kind": "Secret",
            "metadata": _meta(name, ns, {"kubernetes.io/metadata.name": ns}),
            "type": "Opaque", "data": data}


def _pvc(name, ns, size, phase, sc):
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": _meta(name, ns),
            "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": sc,
                     "resources": {"requests": {"storage": size}},
                     **({"volumeName": "pvc-" + name} if phase == "Bound" else {})},
            "status": {"phase": phase,
                       **({"capacity": {"storage": size}, "accessModes": ["ReadWriteOnce"]} if phase == "Bound" else {})}}


def _pv(name, size, phase, sc):
    return {"apiVersion": "v1", "kind": "PersistentVolume",
            "metadata": _meta(name, extra={"labels": {"type": "local"}}),
            "spec": {"capacity": {"storage": size}, "accessModes": ["ReadWriteOnce"],
                     "persistentVolumeReclaimPolicy": "Delete", "storageClassName": sc,
                     "claimRef": {"namespace": "default", "name": name}},
            "status": {"phase": phase}}


def _ev(involved, ns, typ, reason, message, count):
    return {"apiVersion": "v1", "kind": "Event",
            "metadata": _meta(f"{involved}.{uuid.uuid4().hex[:8]}", ns, None, {"annotations": {}}),
            "type": typ, "reason": reason, "message": message, "count": count,
            "involvedObject": {"kind": "Pod" if ns else "Node", "name": involved, "namespace": ns},
            "firstTimestamp": "2026-08-01T08:00:00Z",
            "lastTimestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": {"component": "kubelet" if ns else "node-controller"}}


# ---------------------------------------------------------------------------
# 演示后端
# ---------------------------------------------------------------------------
class DemoBackend:
    """与 KubeClient 接口一致的内存实现。"""

    def __init__(self):
        self.data = build_demo_state()
        self.reason = "未找到可用 kubeconfig"
        self._context = "demo-context (内置演示集群)"

    # --- 会话信息 ---
    @property
    def connected(self):
        return False

    def state(self):
        return {"connected": False, "demo": True, "context": self._context,
                "kubeconfig": None, "error": self.reason}

    def connect(self, path=None):
        return {"context": self._context, "demo": True}

    def save_uploaded_kubeconfig(self, text):
        raise K8sError(400, "演示模式下无法导入 kubeconfig，请先让后端连上真实集群")

    def version(self):
        return {"major": "1", "minor": "29", "gitVersion": "v1.29.4", "platform": "linux/amd64",
                "demo": True}

    def api_resources(self):
        return {"core": {"resources": [{"name": k} for k in RESOURCES]}, "groups": []}

    # --- 通用 CRUD ---
    def list_resource(self, key, namespace=None, label_selector=None, field_selector=None, limit=None):
        rkey, meta = resolve(key)
        items = self.data.get(rkey, [])
        if meta["namespaced"] and namespace:
            items = [i for i in items if (i.get("metadata") or {}).get("namespace") == namespace]
        if label_selector:
            k, _, v = label_selector.partition("=")
            items = [i for i in items if ((i.get("metadata") or {}).get("labels") or {}).get(k) == v]
        return {"resource": rkey, "kind": meta["kind"], "items": copy.deepcopy(items), "count": len(items)}

    def get_resource(self, key, name, namespace=None):
        rkey, meta = resolve(key)
        for i in self.data.get(rkey, []):
            m = i.get("metadata") or {}
            if m.get("name") == name and (not meta["namespaced"] or not namespace or m.get("namespace") == namespace):
                return copy.deepcopy(i)
        raise K8sError(404, f'{meta["kind"]} "{name}" 不存在')

    def delete_resource(self, key, name, namespace=None, grace=None):
        rkey, meta = resolve(key)
        before = len(self.data.get(rkey, []))
        self.data[rkey] = [
            i for i in self.data.get(rkey, [])
            if not ((i.get("metadata") or {}).get("name") == name
                    and (not namespace or (i.get("metadata") or {}).get("namespace") == namespace))
        ]
        if len(self.data[rkey]) == before:
            raise K8sError(404, f'{meta["kind"]} "{name}" 不存在')
        return {"kind": "Status", "status": "Success", "demo": True}

    def patch_resource(self, key, name, namespace, patch, patch_type="merge"):
        obj = self.get_resource(key, name, namespace)
        merged = _deep_merge(obj, patch)
        rkey, meta = resolve(key)
        for idx, i in enumerate(self.data[rkey]):
            if (i.get("metadata") or {}).get("name") == name:
                self.data[rkey][idx] = merged
                break
        return copy.deepcopy(merged)

    def create_resource(self, manifest):
        kind = manifest.get("kind")
        rkey = next((k for k, m in RESOURCES.items() if m["kind"] == kind), None)
        if not rkey:
            raise K8sError(400, f"不支持的 kind: {kind}")
        self.data.setdefault(rkey, []).append(copy.deepcopy(manifest))
        return copy.deepcopy(manifest)

    def replace_resource(self, manifest):
        kind = manifest.get("kind")
        name = (manifest.get("metadata") or {}).get("name")
        rkey = next((k for k, m in RESOURCES.items() if m["kind"] == kind), None)
        if not rkey:
            raise K8sError(400, f"不支持的 kind: {kind}")
        for idx, i in enumerate(self.data.get(rkey, [])):
            if (i.get("metadata") or {}).get("name") == name:
                self.data[rkey][idx] = copy.deepcopy(manifest)
                return copy.deepcopy(manifest)
        self.data.setdefault(rkey, []).append(copy.deepcopy(manifest))
        return copy.deepcopy(manifest)

    # --- 运维动作 ---
    def scale(self, key, name, namespace, replicas):
        rkey, meta = resolve(key)
        if rkey not in SCALABLE:
            raise K8sError(400, f'{meta["kind"]} 不支持扩缩容')
        obj = self.patch_resource(rkey, name, namespace, {"spec": {"replicas": int(replicas)}})
        obj.setdefault("status", {})["replicas"] = int(replicas)
        obj["status"]["readyReplicas"] = min(int(replicas), obj["status"].get("readyReplicas", int(replicas)))
        self.patch_resource(rkey, name, namespace, {"status": obj["status"]})
        return obj

    def restart(self, key, name, namespace):
        rkey, meta = resolve(key)
        if rkey not in RESTARTABLE:
            raise K8sError(400, f'{meta["kind"]} 不支持重启')
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self.patch_resource(
            rkey, name, namespace,
            {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}},
        )

    def node_action(self, name, action):
        if action not in ("cordon", "uncordon"):
            raise K8sError(400, f"不支持的节点操作: {action}")
        return self.patch_resource("nodes", name, None, {"spec": {"unschedulable": action == "cordon"}})

    def pod_containers(self, namespace, name):
        pod = self.get_resource("pods", name, namespace)
        statuses = (pod.get("status") or {}).get("containerStatuses", []) or []
        return [
            {"name": c.get("name"), "image": c.get("image"),
             "ready": next((s.get("ready") for s in statuses if s.get("name") == c.get("name")), None),
             "restarts": next((s.get("restartCount") for s in statuses if s.get("name") == c.get("name")), 0)}
            for c in (pod.get("spec") or {}).get("containers", [])
        ]

    def pod_logs(self, namespace, name, container=None, tail=200, previous=False):
        pod = self.get_resource("pods", name, namespace)
        issues = pod_issues(pod)
        cname = container or ((pod.get("spec") or {}).get("containers") or [{}])[0].get("name", "app")
        lines = [f"[demo] {name} 容器 {cname} 日志（内置演示数据）"]
        if issues:
            lines.append("检测到异常：" + "; ".join(i["reason"] for i in issues))
            for i in issues:
                if i["message"]:
                    lines.append(f"  → {i['message']}")
            lines.append("最近一次退出码：1")
        else:
            lines.append("服务启动成功，监听 :8080")
        lines.append("GET /healthz 200 1ms")
        return "\n".join(lines)

    def pod_exec(self, namespace, name, command, container=None):
        return f"[demo] 已在 {namespace}/{name} 执行：{' '.join(command)}\n(demo 模式不返回真实输出)"

    # --- 总览 ---
    def cluster_overview(self):
        pods = self.data.get("pods", [])
        nodes = self.data.get("nodes", [])
        phase_count = {}
        for p in pods:
            phase = (p.get("status") or {}).get("phase", "Unknown")
            phase_count[phase] = phase_count.get(phase, 0) + 1

        abnormal = summarize(pods, "Pod")
        abnormal_nodes = summarize(nodes, "Node")
        abnormal_workloads = []
        for rkey, kind in (("deployments", "Deployment"), ("statefulsets", "StatefulSet"), ("daemonsets", "DaemonSet")):
            abnormal_workloads.extend(summarize(self.data.get(rkey, []), kind))

        return {
            "version": self.version(),
            "context": self._context,
            "demo": True,
            "demoReason": self.reason,
            "counts": {
                "nodes": len(nodes),
                "readyNodes": sum(1 for n in nodes if not node_issues(n)),
                "namespaces": len(self.data.get("namespaces", [])),
                "pods": len(pods),
                "deployments": len(self.data.get("deployments", [])),
                "services": len(self.data.get("services", [])),
            },
            "podPhases": phase_count,
            "abnormalPods": abnormal[:50],
            "abnormalNodes": abnormal_nodes,
            "abnormalWorkloads": abnormal_workloads,
            "abnormalCount": len(abnormal) + len(abnormal_nodes) + len(abnormal_workloads),
        }


def _deep_merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
