"""K8s 资源注册表：把 URL 里的资源名映射到官方 client 的具体 Api 与调用方法。

设计目标：一套通用的 list / get / create / replace / patch / delete 逻辑，
覆盖常用资源，避免为每种资源写一遍重复代码。
"""

from __future__ import annotations

from dataclasses import dataclass

from kubernetes import client


@dataclass(frozen=True)
class ResourceSpec:
    name: str  # URL 中使用的复数名，如 deployments
    singular: str  # client 方法中使用的单数名，如 deployment
    kind: str  # 资源 Kind，如 Deployment
    api: str  # 内部 Api 标识
    namespaced: bool = True
    label: str = ""  # 中文显示名


# 单数名与 Api 分组对应关系（与官方 python client 的方法名一致）
RESOURCES: dict[str, ResourceSpec] = {}


def _reg(spec: ResourceSpec) -> None:
    RESOURCES[spec.name] = spec


# --- core/v1 ---------------------------------------------------------------
_reg(ResourceSpec("namespaces", "namespace", "Namespace", "core", False, "命名空间"))
_reg(ResourceSpec("nodes", "node", "Node", "core", False, "节点"))
_reg(ResourceSpec("pods", "pod", "Pod", "core", True, "容器组"))
_reg(ResourceSpec("services", "service", "Service", "core", True, "服务"))
_reg(ResourceSpec("endpoints", "endpoints", "Endpoints", "core", True, "端点"))
_reg(ResourceSpec("configmaps", "config_map", "ConfigMap", "core", True, "配置字典"))
_reg(ResourceSpec("secrets", "secret", "Secret", "core", True, "保密字典"))
_reg(ResourceSpec("serviceaccounts", "service_account", "ServiceAccount", "core", True, "服务账户"))
_reg(ResourceSpec("events", "event", "Event", "core", True, "事件"))
_reg(ResourceSpec("persistentvolumeclaims", "persistent_volume_claim", "PersistentVolumeClaim", "core", True, "存储声明"))
_reg(ResourceSpec("persistentvolumes", "persistent_volume", "PersistentVolume", "core", False, "存储卷"))
_reg(ResourceSpec("resourcequotas", "resource_quota", "ResourceQuota", "core", True, "资源配额"))
_reg(ResourceSpec("limitranges", "limit_range", "LimitRange", "core", True, "限额范围"))

# --- apps/v1 ---------------------------------------------------------------
_reg(ResourceSpec("deployments", "deployment", "Deployment", "apps", True, "部署"))
_reg(ResourceSpec("statefulsets", "stateful_set", "StatefulSet", "apps", True, "有状态副本集"))
_reg(ResourceSpec("daemonsets", "daemon_set", "DaemonSet", "apps", True, "守护进程集"))
_reg(ResourceSpec("replicasets", "replica_set", "ReplicaSet", "apps", True, "副本集"))

# --- batch/v1 --------------------------------------------------------------
_reg(ResourceSpec("jobs", "job", "Job", "batch", True, "任务"))
_reg(ResourceSpec("cronjobs", "cron_job", "CronJob", "batch", True, "定时任务"))

# --- networking.k8s.io/v1 --------------------------------------------------
_reg(ResourceSpec("ingresses", "ingress", "Ingress", "networking", True, "路由"))
_reg(ResourceSpec("networkpolicies", "network_policy", "NetworkPolicy", "networking", True, "网络策略"))

# --- storage.k8s.io/v1 -----------------------------------------------------
_reg(ResourceSpec("storageclasses", "storage_class", "StorageClass", "storage", False, "存储类"))

# --- rbac.authorization.k8s.io/v1 ------------------------------------------
_reg(ResourceSpec("roles", "role", "Role", "rbac", True, "角色"))
_reg(ResourceSpec("rolebindings", "role_binding", "RoleBinding", "rbac", True, "角色绑定"))
_reg(ResourceSpec("clusterroles", "cluster_role", "ClusterRole", "rbac", False, "集群角色"))
_reg(ResourceSpec("clusterrolebindings", "cluster_role_binding", "ClusterRoleBinding", "rbac", False, "集群角色绑定"))

# --- autoscaling/v2 --------------------------------------------------------
_reg(ResourceSpec("horizontalpodautoscalers", "horizontal_pod_autoscaler", "HorizontalPodAutoscaler", "autoscaling", True, "弹性伸缩"))

# 资源名别名，方便前端/自然语言里用简称
ALIASES = {
    "pod": "pods",
    "po": "pods",
    "deploy": "deployments",
    "deployment": "deployments",
    "sts": "statefulsets",
    "statefulset": "statefulsets",
    "ds": "daemonsets",
    "daemonset": "daemonsets",
    "svc": "services",
    "service": "services",
    "ns": "namespaces",
    "namespace": "namespaces",
    "no": "nodes",
    "node": "nodes",
    "cm": "configmaps",
    "configmap": "configmaps",
    "ing": "ingresses",
    "ingress": "ingresses",
    "pvc": "persistentvolumeclaims",
    "pv": "persistentvolumes",
    "cronjob": "cronjobs",
    "job": "jobs",
    "secret": "secrets",
    "sa": "serviceaccounts",
    "hpa": "horizontalpodautoscalers",
    "ev": "events",
    "event": "events",
}


def normalize(name: str) -> str | None:
    """把任意写法归一到注册表 key，未注册返回 None。"""
    if not name:
        return None
    key = name.strip().lower()
    if key in RESOURCES:
        return key
    if key in ALIASES:
        return ALIASES[key]
    return None


def get_spec(name: str) -> ResourceSpec | None:
    key = normalize(name)
    return RESOURCES.get(key) if key else None


def build_api(api: str):
    """按内部标识构造对应的 Api 对象。"""
    mapping = {
        "core": client.CoreV1Api,
        "apps": client.AppsV1Api,
        "batch": client.BatchV1Api,
        "networking": client.NetworkingV1Api,
        "storage": client.StorageV1Api,
        "rbac": client.RbacAuthorizationV1Api,
        "autoscaling": client.AutoscalingV2Api,
    }
    factory = mapping.get(api)
    if factory is None:
        raise ValueError(f"不支持的 Api 分组: {api}")
    return factory()


def serialize(obj):
    """把 client 返回的模型对象转成可 JSON 序列化的 dict。"""
    if obj is None:
        return None
    return client.ApiClient().sanitize_for_serialization(obj)


def object_meta(obj: dict) -> dict:
    return (obj or {}).get("metadata", {}) or {}
