"""内置演示集群。

当 kubeconfig 不存在（或集群不可达）时启用，保证管理平台与 AI 助手
在没有真实集群的情况下也能完整演示、并且操作（扩缩容/重启/删除）真的生效。
数据保存在进程内存中。
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timezone

from .registry import RESOURCES, normalize, object_meta

_lock = threading.RLock()


def _now(offset_minutes: int = 0) -> str:
    ts = datetime.now(timezone.utc).timestamp() - offset_minutes * 60
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _container(name, image, cpu="100m", mem="128Mi", cpu_l="500m", mem_l="512Mi"):
    return {
        "name": name,
        "image": image,
        "ports": [{"containerPort": 80, "protocol": "TCP"}],
        "resources": {
            "requests": {"cpu": cpu, "memory": mem},
            "limits": {"cpu": cpu_l, "memory": mem_l},
        },
        "env": [{"name": "ENV", "value": "prod"}],
        "volumeMounts": [],
    }


def _pod(
    name,
    ns,
    node,
    phase,
    containers,
    ready=True,
    restarts=0,
    reason=None,
    message=None,
    labels=None,
    owner=None,
    age=30,
    pod_ip="10.244.1.10",
    ready_containers=None,
):
    ready_containers = ready if ready_containers is None else ready_containers
    cs = []
    for c in containers:
        state = {"running": {"startedAt": _now(age)}}
        last = None
        waiting = None
        if reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"):
            if reason == "CrashLoopBackOff":
                state = {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 5m0s restarting failed container"}}
                last = {"terminated": {"exitCode": 1, "reason": "Error", "finishedAt": _now(2)}}
            else:
                state = {"waiting": {"reason": reason, "message": message or "Back-off pulling image"}}
        elif reason == "OOMKilled":
            state = {"waiting": {"reason": "CrashLoopBackOff", "message": "container is OOMKilled"}}
            last = {"terminated": {"exitCode": 137, "reason": "OOMKilled", "finishedAt": _now(3)}}
        elif phase == "Succeeded":
            state = {"terminated": {"exitCode": 0, "reason": "Completed", "finishedAt": _now(1)}}
        cs.append(
            {
                "name": c["name"],
                "image": c["image"],
                "ready": ready_containers and state.get("running") is not None,
                "restartCount": restarts,
                "state": state,
                "lastState": last or {},
                "imagePullPolicy": "IfNotPresent",
            }
        )

    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": ns,
            "uid": f"mock-{name}",
            "creationTimestamp": _now(age),
            "labels": labels or {"app": name.rsplit("-", 2)[0]},
            "annotations": {},
        },
        "spec": {
            "nodeName": node,
            "containers": containers,
            "restartPolicy": "Always",
            "serviceAccountName": "default",
            "volumes": [],
        },
        "status": {
            "phase": phase,
            "podIP": pod_ip,
            "hostIP": "192.168.234.10",
            "startTime": _now(age),
            "containerStatuses": cs,
            "conditions": [
                {"type": "Ready", "status": "True" if ready else "False"},
                {"type": "PodScheduled", "status": "True"},
            ],
        },
    }
    if owner:
        pod["metadata"]["ownerReferences"] = [
            {
                "apiVersion": owner["apiVersion"],
                "kind": owner["kind"],
                "name": owner["name"],
                "uid": owner["name"],
                "controller": True,
            }
        ]
    return pod


def _build_data() -> dict:
    data: dict[str, list[dict]] = {}

    namespaces = ["default", "kube-system", "kube-public", "dev", "prod", "monitoring"]
    data["namespaces"] = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": n, "uid": f"ns-{n}", "creationTimestamp": _now(60 * 24 * 30), "labels": {}},
            "status": {"phase": "Active"},
        }
        for n in namespaces
    ]

    data["nodes"] = [
        {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": "k8s-master01",
                "uid": "node-master",
                "creationTimestamp": _now(60 * 24 * 40),
                "labels": {"kubernetes.io/hostname": "k8s-master01", "node-role.kubernetes.io/control-plane": ""},
                "annotations": {},
            },
            "spec": {"unschedulable": False, "podCIDR": "10.244.0.0/24"},
            "status": {
                "addresses": [{"type": "InternalIP", "address": "192.168.234.10"}],
                "nodeInfo": {"kubeletVersion": "v1.29.2", "osImage": "Rocky Linux 9.3", "containerRuntimeVersion": "containerd://1.7.13"},
                "capacity": {"cpu": "8", "memory": "16Gi", "pods": "110"},
                "allocatable": {"cpu": "7800m", "memory": "15Gi", "pods": "110"},
                "conditions": [{"type": "Ready", "status": "True", "lastHeartbeatTime": _now(0)}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": "k8s-node01",
                "uid": "node-1",
                "creationTimestamp": _now(60 * 24 * 40),
                "labels": {"kubernetes.io/hostname": "k8s-node01"},
                "annotations": {},
            },
            "spec": {"unschedulable": False, "podCIDR": "10.244.1.0/24"},
            "status": {
                "addresses": [{"type": "InternalIP", "address": "192.168.234.11"}],
                "nodeInfo": {"kubeletVersion": "v1.29.2", "osImage": "Rocky Linux 9.3", "containerRuntimeVersion": "containerd://1.7.13"},
                "capacity": {"cpu": "8", "memory": "16Gi", "pods": "110"},
                "allocatable": {"cpu": "7800m", "memory": "15Gi", "pods": "110"},
                "conditions": [{"type": "Ready", "status": "True", "lastHeartbeatTime": _now(0)}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": "k8s-node02",
                "uid": "node-2",
                "creationTimestamp": _now(60 * 24 * 12),
                "labels": {"kubernetes.io/hostname": "k8s-node02"},
                "annotations": {},
            },
            "spec": {"unschedulable": False, "podCIDR": "10.244.2.0/24"},
            "status": {
                "addresses": [{"type": "InternalIP", "address": "192.168.234.12"}],
                "nodeInfo": {"kubeletVersion": "v1.29.2", "osImage": "Rocky Linux 9.3", "containerRuntimeVersion": "containerd://1.7.13"},
                "capacity": {"cpu": "4", "memory": "8Gi", "pods": "110"},
                "allocatable": {"cpu": "3800m", "memory": "7Gi", "pods": "110"},
                "conditions": [{"type": "Ready", "status": "False", "reason": "KubeletNotReady", "message": "container runtime is down", "lastHeartbeatTime": _now(12)}],
            },
        },
    ]

    # 部署（含一个副本数不足、一个镜像错误的场景）
    deployments = [
        {
            "name": "nginx-web",
            "ns": "prod",
            "replicas": 3,
            "ready": 3,
            "image": "nginx:1.25-alpine",
            "port": 80,
        },
        {
            "name": "api-server",
            "ns": "prod",
            "replicas": 3,
            "ready": 1,
            "image": "registry.local/api-server:v2.3.1",
            "port": 8080,
        },
        {
            "name": "redis",
            "ns": "dev",
            "replicas": 1,
            "ready": 1,
            "image": "redis:7.2-alpine",
            "port": 6379,
        },
        {
            "name": "broken-app",
            "ns": "dev",
            "replicas": 2,
            "ready": 0,
            "image": "registry.local/does-not-exist:v9",
            "port": 8080,
        },
    ]

    data["deployments"] = []
    pods: list[dict] = []

    for d in deployments:
        owner = {"apiVersion": "apps/v1", "kind": "Deployment", "name": d["name"]}
        data["deployments"].append(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": d["name"],
                    "namespace": d["ns"],
                    "uid": f"deploy-{d['name']}",
                    "creationTimestamp": _now(60 * 24 * 5),
                    "labels": {"app": d["name"]},
                    "annotations": {},
                },
                "spec": {
                    "replicas": d["replicas"],
                    "selector": {"matchLabels": {"app": d["name"]}},
                    "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}},
                    "template": {
                        "metadata": {"labels": {"app": d["name"]}},
                        "spec": {
                            "containers": [_container(d["name"], d["image"])],
                            "restartPolicy": "Always",
                        },
                    },
                },
                "status": {
                    "replicas": d["replicas"],
                    "readyReplicas": d["ready"],
                    "availableReplicas": d["ready"],
                    "updatedReplicas": d["replicas"],
                    "unavailableReplicas": d["replicas"] - d["ready"],
                    "conditions": [{"type": "Available", "status": "True" if d["ready"] == d["replicas"] else "False"}],
                },
            }
        )

        # 生成副本 Pod
        if d["name"] == "api-server":
            # 1 个正常 + 1 个 CrashLoopBackOff + 1 个 OOMKilled
            pods.append(_pod(f"{d['name']}-6d9f7c8b4-2xk9p", d["ns"], "k8s-node01", "Running",
                             [_container(d["name"], d["image"], "200m", "256Mi", "500m", "512Mi")],
                             ready=True, restarts=0, owner=owner, age=45, pod_ip="10.244.1.21"))
            pods.append(_pod(f"{d['name']}-6d9f7c8b4-lq8wm", d["ns"], "k8s-node01", "Running",
                             [_container(d["name"], d["image"], "200m", "256Mi", "500m", "512Mi")],
                             ready=False, restarts=47, reason="CrashLoopBackOff", owner=owner, age=38, pod_ip="10.244.1.22"))
            pods.append(_pod(f"{d['name']}-6d9f7c8b4-t7vbn", d["ns"], "k8s-node02", "Running",
                             [_container(d["name"], d["image"], "200m", "256Mi", "500m", "512Mi")],
                             ready=False, restarts=12, reason="OOMKilled", owner=owner, age=20, pod_ip="10.244.2.11"))
        elif d["name"] == "broken-app":
            for i in range(d["replicas"]):
                pods.append(_pod(f"{d['name']}-5f8c9d6b7-abc{i}", d["ns"], "k8s-node01", "Pending",
                                 [_container(d["name"], d["image"])],
                                 ready=False, restarts=0, reason="ImagePullBackOff",
                                 message=f'Failed to pull image "{d["image"]}": not found',
                                 owner=owner, age=15, pod_ip=""))
        else:
            for i in range(d["replicas"]):
                pods.append(_pod(f"{d['name']}-{i}0d4f5e6-xyz{i}", d["ns"], "k8s-node01", "Running",
                                 [_container(d["name"], d["image"])],
                                 ready=True, restarts=0, owner=owner, age=120 + i, pod_ip=f"10.244.1.3{i}"))

    # 一个处于 Pending 的资源不足 Pod
    pods.append(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "pending-scheduler-test",
                "namespace": "default",
                "uid": "mock-pending",
                "creationTimestamp": _now(8),
                "labels": {"app": "sched-test"},
                "annotations": {},
            },
            "spec": {"containers": [_container("test", "busybox:1.36")], "restartPolicy": "Always"},
            "status": {
                "phase": "Pending",
                "containerStatuses": [],
                "conditions": [
                    {"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                     "message": "0/3 nodes are available: 2 Insufficient cpu, 1 node(s) had untolerated taint."}
                ],
            },
        }
    )

    # kube-system 里的系统组件
    for i, name in enumerate(["kube-apiserver-k8s-master01", "etcd-k8s-master01", "kube-scheduler-k8s-master01"]):
        pods.append(_pod(name, "kube-system", "k8s-master01", "Running",
                         [_container(name, f"registry.k8s.io/{name.split('-')[1]}:v1.29.2")],
                         ready=True, restarts=1, age=60 * 24 * 40, pod_ip="192.168.234.10"))
    pods.append(_pod("calico-node-9x2kd", "kube-system", "k8s-node02", "Running",
                     [_container("calico-node", "calico/node:v3.27.0")],
                     ready=True, restarts=3, age=60 * 24 * 12, pod_ip="192.168.234.12"))

    data["pods"] = pods

    data["services"] = [
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "nginx-web", "namespace": "prod", "uid": "svc-nginx", "creationTimestamp": _now(60 * 24 * 5), "labels": {"app": "nginx-web"}},
            "spec": {"type": "ClusterIP", "clusterIP": "10.96.12.34", "selector": {"app": "nginx-web"},
                     "ports": [{"name": "http", "port": 80, "targetPort": 80, "protocol": "TCP", "nodePort": 30080}]},
            "status": {"loadBalancer": {}},
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "api-server", "namespace": "prod", "uid": "svc-api", "creationTimestamp": _now(60 * 24 * 5), "labels": {"app": "api-server"}},
            "spec": {"type": "NodePort", "clusterIP": "10.96.12.35", "selector": {"app": "api-server"},
                     "ports": [{"name": "http", "port": 8080, "targetPort": 8080, "protocol": "TCP", "nodePort": 31080}]},
            "status": {"loadBalancer": {}},
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "kubernetes", "namespace": "default", "uid": "svc-k8s", "creationTimestamp": _now(60 * 24 * 40), "labels": {}},
            "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.1", "ports": [{"name": "https", "port": 443, "targetPort": 6443, "protocol": "TCP"}]},
            "status": {"loadBalancer": {}},
        },
    ]

    data["configmaps"] = [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "api-server-config", "namespace": "prod", "uid": "cm-api", "creationTimestamp": _now(60 * 24 * 5)},
            "data": {"LOG_LEVEL": "info", "TIMEOUT": "30s", "DB_HOST": "mysql.prod.svc.cluster.local"},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "kube-root-ca.crt", "namespace": "default", "uid": "cm-ca", "creationTimestamp": _now(60 * 24 * 40)},
            "data": {"ca.crt": "-----BEGIN CERTIFICATE-----\nMIIB...mock...\n-----END CERTIFICATE-----"},
        },
    ]

    data["secrets"] = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "api-server-secret", "namespace": "prod", "uid": "sec-api", "creationTimestamp": _now(60 * 24 * 5)},
            "type": "Opaque",
            "data": {"DB_PASSWORD": "bW9jay1wYXNzd29yZA==", "JWT_SECRET": "bW9jay1qd3Q="},
        },
    ]

    data["ingresses"] = [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "web-ingress", "namespace": "prod", "uid": "ing-1", "creationTimestamp": _now(60 * 24 * 3)},
            "spec": {
                "ingressClassName": "nginx",
                "rules": [{"host": "web.example.com", "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "nginx-web", "port": {"number": 80}}}}]}}],
                "tls": [{"hosts": ["web.example.com"], "secretName": "web-tls"}],
            },
            "status": {"loadBalancer": {"ingress": [{"ip": "192.168.234.10"}]}},
        }
    ]

    data["persistentvolumeclaims"] = [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "redis-data", "namespace": "dev", "uid": "pvc-1", "creationTimestamp": _now(60 * 24 * 10)},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}, "storageClassName": "local-path", "volumeName": "pv-1"},
            "status": {"phase": "Bound", "capacity": {"storage": "10Gi"}},
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "orphan-claim", "namespace": "dev", "uid": "pvc-2", "creationTimestamp": _now(60 * 2)},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "20Gi"}}, "storageClassName": "nfs-storage"},
            "status": {"phase": "Pending"},
        },
    ]

    data["storageclasses"] = [
        {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
         "metadata": {"name": "local-path", "uid": "sc-1", "creationTimestamp": _now(60 * 24 * 40), "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}},
         "provisioner": "rancher.io/local-path", "reclaimPolicy": "Delete", "volumeBindingMode": "WaitForFirstConsumer"},
        {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
         "metadata": {"name": "nfs-storage", "uid": "sc-2", "creationTimestamp": _now(60 * 24 * 20), "annotations": {}},
         "provisioner": "example.com/nfs", "reclaimPolicy": "Retain", "volumeBindingMode": "Immediate"},
    ]

    data["jobs"] = [
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "db-migrate", "namespace": "prod", "uid": "job-1", "creationTimestamp": _now(180)},
            "spec": {"completions": 1, "parallelism": 1, "template": {"spec": {"containers": [_container("migrate", "registry.local/migrate:v1")], "restartPolicy": "Never"}}},
            "status": {"succeeded": 1, "startTime": _now(180), "completionTime": _now(175)},
        }
    ]

    data["cronjobs"] = [
        {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": {"name": "backup-nightly", "namespace": "prod", "uid": "cj-1", "creationTimestamp": _now(60 * 24 * 30)},
            "spec": {"schedule": "0 2 * * *", "suspend": False, "jobTemplate": {"spec": {"template": {"spec": {"containers": [_container("backup", "registry.local/backup:v2")], "restartPolicy": "OnFailure"}}}}},
            "status": {"lastScheduleTime": _now(60 * 10)},
        }
    ]

    data["serviceaccounts"] = [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "default", "namespace": n, "uid": f"sa-default-{n}", "creationTimestamp": _now(60 * 24 * 40)}, "secrets": []}
        for n in ["default", "dev", "prod"]
    ]

    # 事件：含异常事件，供 AI 诊断
    data["events"] = [
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "api-server-6d9f7c8b4-lq8wm.17f2a", "namespace": "prod", "uid": "ev-1", "creationTimestamp": _now(4)},
         "involvedObject": {"kind": "Pod", "name": "api-server-6d9f7c8b4-lq8wm", "namespace": "prod"},
         "reason": "BackOff", "type": "Warning",
         "message": "Back-off restarting failed container api-server in pod api-server-6d9f7c8b4-lq8wm_prod", "count": 47},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "api-server-6d9f7c8b4-t7vbn.17f3b", "namespace": "prod", "uid": "ev-2", "creationTimestamp": _now(3)},
         "involvedObject": {"kind": "Pod", "name": "api-server-6d9f7c8b4-t7vbn", "namespace": "prod"},
         "reason": "OOMKilling", "type": "Warning",
         "message": "Memory cgroup out of memory: Killed process (api-server) total-vm:1048576kB", "count": 12},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "broken-app-5f8c9d6b7-abc0.17f4c", "namespace": "dev", "uid": "ev-3", "creationTimestamp": _now(14)},
         "involvedObject": {"kind": "Pod", "name": "broken-app-5f8c9d6b7-abc0", "namespace": "dev"},
         "reason": "Failed", "type": "Warning",
         "message": 'Failed to pull image "registry.local/does-not-exist:v9": rpc error: code = NotFound', "count": 18},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "pending-scheduler-test.17f5d", "namespace": "default", "uid": "ev-4", "creationTimestamp": _now(8)},
         "involvedObject": {"kind": "Pod", "name": "pending-scheduler-test", "namespace": "default"},
         "reason": "FailedScheduling", "type": "Warning",
         "message": "0/3 nodes are available: 2 Insufficient cpu, 1 node(s) had untolerated taint.", "count": 22},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "k8s-node02.17f6e", "namespace": "default", "uid": "ev-5", "creationTimestamp": _now(12)},
         "involvedObject": {"kind": "Node", "name": "k8s-node02"},
         "reason": "NodeNotReady", "type": "Warning",
         "message": "Node k8s-node02 status is now: NodeNotReady", "count": 5},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "orphan-claim.17f7f", "namespace": "dev", "uid": "ev-6", "creationTimestamp": _now(60 * 24)},
         "involvedObject": {"kind": "PersistentVolumeClaim", "name": "orphan-claim", "namespace": "dev"},
         "reason": "ProvisioningFailed", "type": "Warning",
         "message": 'storageclass "nfs-storage" not found / no persistent volumes available for this claim', "count": 30},
        {"apiVersion": "v1", "kind": "Event",
         "metadata": {"name": "nginx-web.17f8g", "namespace": "prod", "uid": "ev-7", "creationTimestamp": _now(120)},
         "involvedObject": {"kind": "Deployment", "name": "nginx-web", "namespace": "prod"},
         "reason": "ScalingReplicaSet", "type": "Normal", "message": "Scaled up replica set nginx-web-abc to 3", "count": 1},
    ]

    for key in RESOURCES:
        data.setdefault(key, [])
    return data


_DATA = _build_data()
MOCK_MODE = True


# ---------------------------------------------------------------------------
# 通用查询 / 变更
# ---------------------------------------------------------------------------
def _match(obj: dict, name: str, namespace: str | None) -> bool:
    meta = object_meta(obj)
    if name and meta.get("name") != name:
        return False
    if namespace and meta.get("namespace") != namespace:
        return False
    return True


def list_objects(res: str, namespace: str | None = None, label_selector: str | None = None):
    key = normalize(res) or res
    with _lock:
        items = copy.deepcopy(_DATA.get(key, []))
    if namespace:
        items = [o for o in items if object_meta(o).get("namespace") == namespace]
    if label_selector:
        items = [o for o in items if _label_match(object_meta(o).get("labels"), label_selector)]
    return items


def _label_match(labels: dict | None, selector: str) -> bool:
    labels = labels or {}
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if labels.get(k.strip()) != v.strip():
            return False
    return True


def get_object(res: str, name: str, namespace: str | None = None):
    key = normalize(res) or res
    with _lock:
        for obj in _DATA.get(key, []):
            if _match(obj, name, namespace):
                return copy.deepcopy(obj)
    return None


def create_object(res: str, body: dict):
    key = normalize(res) or res
    with _lock:
        body = copy.deepcopy(body)
        meta = body.setdefault("metadata", {})
        meta.setdefault("uid", f"mock-{time.time()}")
        meta.setdefault("creationTimestamp", _now(0))
        # 简化 status
        if key == "pods":
            body.setdefault("status", {"phase": "Pending", "containerStatuses": [], "conditions": []})
        _DATA.setdefault(key, []).append(body)
        return copy.deepcopy(body)


def replace_object(res: str, name: str, body: dict, namespace: str | None = None):
    key = normalize(res) or res
    with _lock:
        items = _DATA.setdefault(key, [])
        for i, obj in enumerate(items):
            if _match(obj, name, namespace):
                merged = copy.deepcopy(body)
                merged.setdefault("metadata", {})["uid"] = object_meta(obj).get("uid")
                items[i] = merged
                return copy.deepcopy(merged)
    return None


def patch_object(res: str, name: str, patch: dict, namespace: str | None = None):
    """支持 strategic/merge 的简化实现：dict 递归合并，数组直接替换。"""
    key = normalize(res) or res
    with _lock:
        items = _DATA.setdefault(key, [])
        for i, obj in enumerate(items):
            if _match(obj, name, namespace):
                merged = _deep_merge(copy.deepcopy(obj), patch)
                items[i] = merged
                return copy.deepcopy(merged)
    return None


def _deep_merge(base: dict, patch: dict) -> dict:
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def delete_object(res: str, name: str, namespace: str | None = None) -> bool:
    key = normalize(res) or res
    with _lock:
        items = _DATA.setdefault(key, [])
        for i, obj in enumerate(items):
            if _match(obj, name, namespace):
                items.pop(i)
                return True
    return False


def update_scale(res: str, name: str, replicas: int, namespace: str | None = None):
    """扩缩容：改 spec.replicas，并同步 status 里的副本数。"""
    key = normalize(res) or res
    with _lock:
        items = _DATA.setdefault(key, [])
        for i, obj in enumerate(items):
            if _match(obj, name, namespace):
                obj.setdefault("spec", {})["replicas"] = replicas
                current_ready = (obj.get("status") or {}).get("readyReplicas", 0)
                obj.setdefault("status", {})["replicas"] = replicas
                # 演示环境下按现有可用副本数封顶
                ready = min(replicas, max(current_ready, 0))
                if replicas == 0:
                    ready = 0
                obj["status"]["readyReplicas"] = ready
                obj["status"]["availableReplicas"] = ready
                obj["status"]["unavailableReplicas"] = max(replicas - ready, 0)
                items[i] = obj
                return copy.deepcopy(obj)
    return None


def restart_workload(res: str, name: str, namespace: str | None = None):
    """重启：给 Pod 模板打上 restartAt 注解，等价于 kubectl rollout restart。"""
    key = normalize(res) or res
    stamp = _now(0)
    with _lock:
        items = _DATA.setdefault(key, [])
        for i, obj in enumerate(items):
            if _match(obj, name, namespace):
                template = obj.setdefault("spec", {}).setdefault("template", {}).setdefault("metadata", {})
                annotations = template.setdefault("annotations", {})
                annotations["kubectl.kubernetes.io/restartedAt"] = stamp
                items[i] = obj
                return copy.deepcopy(obj)
    return None


def pods_of_workload(res: str, name: str, namespace: str | None = None):
    """按 ownerReferences 找到工作负载下的 Pod。"""
    owner_kind = (RESOURCES.get(normalize(res) or res) or None)
    kind = owner_kind.kind if owner_kind else None
    result = []
    with _lock:
        for pod in _DATA.get("pods", []):
            meta = object_meta(pod)
            if namespace and meta.get("namespace") != namespace:
                continue
            for ref in meta.get("ownerReferences", []) or []:
                if ref.get("name") == name and (kind is None or ref.get("kind") == kind):
                    result.append(copy.deepcopy(pod))
    return result


def node_action(name: str, action: str):
    """cordon / uncordon 节点。"""
    with _lock:
        for node in _DATA.get("nodes", []):
            if object_meta(node).get("name") == name:
                unschedulable = action == "cordon"
                node.setdefault("spec", {})["unschedulable"] = unschedulable
                return copy.deepcopy(node)
    return None


def pod_logs(name: str, namespace: str, container: str | None = None, tail: int = 200):
    """演示日志：针对重启/崩溃的 Pod 给出有诊断价值的日志。"""
    pod = get_object("pods", name, namespace)
    if not pod:
        return None
    phase = (pod.get("status") or {}).get("phase")
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    reason = ""
    for cs in statuses:
        st = cs.get("state") or {}
        if "waiting" in st:
            reason = st["waiting"].get("reason", "")
    lines = [f"[mock] logs for pod {namespace}/{name}"]
    if reason == "CrashLoopBackOff":
        lines += [
            "2026-09-25 03:12:01 INFO  starting api-server v2.3.1",
            "2026-09-25 03:12:01 INFO  connecting to mysql.prod.svc.cluster.local:3306",
            "2026-09-25 03:12:03 ERROR dial tcp 10.96.20.7:3306: connect: connection refused",
            "2026-09-25 03:12:03 FATAL cannot connect to database, exiting",
            "2026-09-25 03:12:03 panic: runtime error: invalid memory address",
        ]
    elif reason in ("ImagePullBackOff", "ErrImagePull"):
        lines.append("(no logs) 容器尚未启动：镜像拉取失败")
    elif reason == "OOMKilled":
        lines += [
            "2026-09-25 03:05:11 INFO  loading large dataset into memory",
            "2026-09-25 03:05:44 WARN  heap usage 480MiB / limit 512MiB",
            "2026-09-25 03:05:59 ERROR out of memory",
            "Killed",
        ]
    elif phase == "Pending":
        lines.append("(no logs) Pod 处于 Pending，尚未被调度")
    else:
        lines += [
            "2026-09-25 03:20:01 INFO  server listening on :80",
            "2026-09-25 03:20:02 INFO  10.244.1.1 - GET /healthz 200",
            "2026-09-25 03:20:07 INFO  10.244.1.1 - GET / 200",
        ]
    return "\n".join(lines[-tail:])
