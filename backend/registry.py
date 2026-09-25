"""资源注册表与公共异常。

单独成模块是为了打破 kube.py 与 demo.py 的循环依赖 —— 两者都依赖这里，
而这里不依赖任何东西。
"""


class K8sError(Exception):
    """统一的 K8s 访问异常，带 HTTP 状态码，便于上层直接映射给前端。"""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _r(name, kind, api, namespaced=True, label=None, group=None):
    return {
        "name": name,
        "kind": kind,
        "api": api,  # REST 前缀，如 /api/v1 或 /apis/apps/v1
        "namespaced": namespaced,
        "label": label or kind,
        "group": group or "core",
    }


RESOURCES = {
    # --- 集群级 ---
    "nodes": _r("nodes", "Node", "/api/v1", False, "节点", "cluster"),
    "namespaces": _r("namespaces", "Namespace", "/api/v1", False, "命名空间", "cluster"),
    "persistentvolumes": _r("persistentvolumes", "PersistentVolume", "/api/v1", False, "持久卷", "storage"),
    "storageclasses": _r("storageclasses", "StorageClass", "/apis/storage.k8s.io/v1", False, "存储类", "storage"),
    "clusterroles": _r("clusterroles", "ClusterRole", "/apis/rbac.authorization.k8s.io/v1", False, "集群角色", "rbac"),
    "clusterrolebindings": _r("clusterrolebindings", "ClusterRoleBinding", "/apis/rbac.authorization.k8s.io/v1", False, "集群角色绑定", "rbac"),

    # --- 工作负载 ---
    "pods": _r("pods", "Pod", "/api/v1", True, "容器组", "workload"),
    "deployments": _r("deployments", "Deployment", "/apis/apps/v1", True, "部署", "workload"),
    "statefulsets": _r("statefulsets", "StatefulSet", "/apis/apps/v1", True, "有状态副本集", "workload"),
    "daemonsets": _r("daemonsets", "DaemonSet", "/apis/apps/v1", True, "守护进程集", "workload"),
    "replicasets": _r("replicasets", "ReplicaSet", "/apis/apps/v1", True, "副本集", "workload"),
    "jobs": _r("jobs", "Job", "/apis/batch/v1", True, "任务", "workload"),
    "cronjobs": _r("cronjobs", "CronJob", "/apis/batch/v1", True, "定时任务", "workload"),
    "horizontalpodautoscalers": _r("horizontalpodautoscalers", "HorizontalPodAutoscaler", "/apis/autoscaling/v2", True, "弹性伸缩", "workload"),

    # --- 配置 ---
    "configmaps": _r("configmaps", "ConfigMap", "/api/v1", True, "配置字典", "config"),
    "secrets": _r("secrets", "Secret", "/api/v1", True, "密钥", "config"),
    "serviceaccounts": _r("serviceaccounts", "ServiceAccount", "/api/v1", True, "服务账户", "config"),

    # --- 网络 ---
    "services": _r("services", "Service", "/api/v1", True, "服务", "network"),
    "endpoints": _r("endpoints", "Endpoints", "/api/v1", True, "端点", "network"),
    "ingresses": _r("ingresses", "Ingress", "/apis/networking.k8s.io/v1", True, "路由", "network"),
    "networkpolicies": _r("networkpolicies", "NetworkPolicy", "/apis/networking.k8s.io/v1", True, "网络策略", "network"),

    # --- 存储 ---
    "persistentvolumeclaims": _r("persistentvolumeclaims", "PersistentVolumeClaim", "/api/v1", True, "存储声明", "storage"),

    # --- 其他 ---
    "events": _r("events", "Event", "/api/v1", True, "事件", "cluster"),
    "roles": _r("roles", "Role", "/apis/rbac.authorization.k8s.io/v1", True, "角色", "rbac"),
    "rolebindings": _r("rolebindings", "RoleBinding", "/apis/rbac.authorization.k8s.io/v1", True, "角色绑定", "rbac"),
}

# 控制器型资源：支持 scale / restart
SCALABLE = {"deployments", "statefulsets", "replicasets"}
RESTARTABLE = {"deployments", "statefulsets", "daemonsets"}

ALIASES = {
    "po": "pods", "pod": "pods",
    "deploy": "deployments", "deployment": "deployments",
    "sts": "statefulsets", "statefulset": "statefulsets",
    "ds": "daemonsets", "daemonset": "daemonsets",
    "svc": "services", "service": "services",
    "ing": "ingresses", "ingress": "ingresses",
    "cm": "configmaps", "configmap": "configmaps",
    "secret": "secrets", "ns": "namespaces", "namespace": "namespaces",
    "node": "nodes", "pv": "persistentvolumes", "pvc": "persistentvolumeclaims",
    "cronjob": "cronjobs", "job": "jobs", "rs": "replicasets",
    "hpa": "horizontalpodautoscalers", "sc": "storageclasses",
}


def resolve(key):
    """按 key/别名/kind 解析资源定义（大小写与单复数宽松匹配）。"""
    if not key:
        raise K8sError(400, "缺少资源类型")
    k = str(key).lower()
    if k in RESOURCES:
        return k, RESOURCES[k]
    if k in ALIASES:
        return resolve(ALIASES[k])
    for name, meta in RESOURCES.items():
        if meta["kind"].lower() == k or meta["name"] == k:
            return name, meta
    raise K8sError(400, f"不支持的资源类型: {key}")


def api_prefix(api_version):
    """把 apiVersion 转成 REST 前缀。"""
    api_version = api_version or "v1"
    if "/" not in api_version:
        return f"/api/{api_version}"
    group, version = api_version.split("/", 1)
    return f"/apis/{group}/{version}"
