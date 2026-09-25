"""集群访问层。

对外暴露统一的 ClusterClient：真实集群（kubernetes 官方 client）与
演示集群（mock）走同一套方法签名，上层路由不需要关心当前是哪种模式。
"""

from __future__ import annotations

import logging
import threading

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException

import config
from . import mock
from .registry import build_api, get_spec, normalize, object_meta, serialize

logger = logging.getLogger("k8s.client")


class ClusterError(Exception):
    """统一的集群访问异常，携带 HTTP 状态码便于路由层返回。"""

    def __init__(self, message: str, status: int = 500, reason: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason


class ClusterClient:
    def __init__(self):
        self._lock = threading.RLock()
        self._loaded = False
        self.mode = "mock"  # mock | real
        self.context = ""
        self.server = ""
        self.error = ""
        self.path = ""

    # ------------------------------------------------------------------
    # 初始化 / 状态
    # ------------------------------------------------------------------
    def load(self, force: bool = False) -> None:
        with self._lock:
            if self._loaded and not force:
                return
            self._loaded = True
            self.error = ""
            self.mode = "mock"

            if config.FORCE_MOCK:
                self.error = "FORCE_MOCK=1，已强制使用演示数据"
                return

            path = config.resolved_kubeconfig()
            if path is None:
                self.error = (
                    f"未找到 kubeconfig（期望路径：{config.KUBECONFIG_PATH}）。"
                    "当前使用内置演示数据。"
                )
                if not config.ALLOW_MOCK_FALLBACK:
                    self.mode = "error"
                return

            self.path = str(path)
            try:
                k8s_config.load_kube_config(
                    config_file=str(path), context=config.KUBE_CONTEXT
                )
                cfg = k8s_client.Configuration.get_default_copy()
                self.server = cfg.host or ""
                self.context = cfg.host and (config.KUBE_CONTEXT or "current-context") or ""
                # 探活：确认能真正连上 API Server
                k8s_client.CoreV1Api().get_api_resources(_request_timeout=8)
                self.mode = "real"
                logger.info("已连接集群：%s (%s)", self.server, path)
            except ApiException as exc:
                self.error = f"集群认证/访问失败：{exc.reason or exc}"
                self.mode = "error" if not config.ALLOW_MOCK_FALLBACK else "mock"
            except Exception as exc:  # 网络不可达、证书错误等
                self.error = f"无法连接集群：{exc}"
                self.mode = "error" if not config.ALLOW_MOCK_FALLBACK else "mock"

    def reload(self) -> dict:
        self.load(force=True)
        return self.status()

    def status(self) -> dict:
        info = {
            "mode": self.mode,
            "kubeconfig": self.path or str(config.KUBECONFIG_PATH),
            "kubeconfigExists": bool(config.resolved_kubeconfig()),
            "context": self.context,
            "server": self.server,
            "error": self.error,
        }
        if self.mode == "real":
            try:
                version = k8s_client.VersionApi().get_code(_request_timeout=8)
                info["version"] = getattr(version, "git_version", "")
            except Exception as exc:
                info["version"] = ""
                info["error"] = f"获取版本失败：{exc}"
        else:
            info["version"] = "v1.29.2 (demo)"
        return info

    def _require_real(self) -> None:
        if self.mode != "real":
            raise ClusterError(
                self.error or "当前处于演示模式，该操作仅在真实集群下可用", status=409
            )

    # ------------------------------------------------------------------
    # 通用方法名拼装
    # ------------------------------------------------------------------
    def _call(self, res: str, op: str, **kwargs):
        spec = get_spec(res)
        if spec is None:
            raise ClusterError(f"不支持的资源类型：{res}", status=400)

        api = build_api(spec.api)
        namespace = kwargs.pop("namespace", None)

        if op == "list":
            if spec.namespaced:
                if namespace:
                    method = f"list_namespaced_{spec.singular}"
                else:
                    method = f"list_{spec.singular}_for_all_namespaces"
            else:
                method = f"list_{spec.singular}"
        elif op == "read":
            method = (
                f"read_namespaced_{spec.singular}" if spec.namespaced else f"read_{spec.singular}"
            )
        elif op == "create":
            method = (
                f"create_namespaced_{spec.singular}" if spec.namespaced else f"create_{spec.singular}"
            )
        elif op == "replace":
            method = (
                f"replace_namespaced_{spec.singular}" if spec.namespaced else f"replace_{spec.singular}"
            )
        elif op == "patch":
            method = (
                f"patch_namespaced_{spec.singular}" if spec.namespaced else f"patch_{spec.singular}"
            )
        elif op == "delete":
            method = (
                f"delete_namespaced_{spec.singular}" if spec.namespaced else f"delete_{spec.singular}"
            )
        else:
            raise ClusterError(f"不支持的操作：{op}", status=400)

        func = getattr(api, method, None)
        if func is None:
            raise ClusterError(f"资源 {spec.kind} 不支持 {op} 操作", status=400)

        if spec.namespaced and namespace and op in ("read", "create", "replace", "patch", "delete"):
            kwargs["namespace"] = namespace

        kwargs.setdefault("_request_timeout", config.REQUEST_TIMEOUT)
        return func(**kwargs)

    # ------------------------------------------------------------------
    # 列表 / 详情
    # ------------------------------------------------------------------
    def list_objects(self, res: str, namespace: str | None = None, label_selector: str | None = None):
        if self.mode != "real":
            return mock.list_objects(res, namespace, label_selector)
        try:
            kwargs = {}
            if label_selector:
                kwargs["label_selector"] = label_selector
            if namespace:
                kwargs["namespace"] = namespace
            result = self._call(res, "list", **kwargs)
            return serialize(result.items)
        except ApiException as exc:
            raise self._wrap(exc)

    def get_object(self, res: str, name: str, namespace: str | None = None):
        if self.mode != "real":
            return mock.get_object(res, name, namespace)
        try:
            result = self._call(res, "read", name=name, namespace=namespace)
            return serialize(result)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise self._wrap(exc)

    # ------------------------------------------------------------------
    # 变更
    # ------------------------------------------------------------------
    def create_object(self, res: str, body: dict, namespace: str | None = None):
        if self.mode != "real":
            return mock.create_object(res, body)
        try:
            return serialize(self._call(res, "create", body=body, namespace=namespace))
        except ApiException as exc:
            raise self._wrap(exc)

    def replace_object(self, res: str, name: str, body: dict, namespace: str | None = None):
        if self.mode != "real":
            return mock.replace_object(res, name, body, namespace)
        try:
            return serialize(self._call(res, "replace", name=name, body=body, namespace=namespace))
        except ApiException as exc:
            raise self._wrap(exc)

    def patch_object(self, res: str, name: str, patch: dict, namespace: str | None = None):
        if self.mode != "real":
            return mock.patch_object(res, name, patch, namespace)
        try:
            return serialize(
                self._call(
                    res,
                    "patch",
                    name=name,
                    body=patch,
                    namespace=namespace,
                    content_type="application/merge-patch+json",
                )
            )
        except ApiException as exc:
            raise self._wrap(exc)

    def delete_object(self, res: str, name: str, namespace: str | None = None):
        if self.mode != "real":
            return mock.delete_object(res, name, namespace)
        try:
            self._call(res, "delete", name=name, namespace=namespace)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise self._wrap(exc)

    # ------------------------------------------------------------------
    # 工作负载动作
    # ------------------------------------------------------------------
    def scale(self, res: str, name: str, replicas: int, namespace: str):
        if self.mode != "real":
            result = mock.update_scale(res, name, replicas, namespace)
            if result is None:
                raise ClusterError("未找到该工作负载", status=404)
            return result
        return self.patch_object(res, name, {"spec": {"replicas": replicas}}, namespace)

    def restart(self, res: str, name: str, namespace: str):
        stamp = _utc_now()
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": stamp}
                    }
                }
            }
        }
        if self.mode != "real":
            result = mock.restart_workload(res, name, namespace)
            if result is None:
                raise ClusterError("未找到该工作负载", status=404)
            return result
        return self.patch_object(res, name, patch, namespace)

    def rollout_status(self, res: str, name: str, namespace: str):
        """部署是否已完成滚动更新。"""
        if self.mode != "real":
            obj = mock.get_object(res, name, namespace)
            status = (obj or {}).get("status", {})
            spec = (obj or {}).get("spec", {})
            desired = spec.get("replicas", 0)
            ready = status.get("readyReplicas", 0)
            return {
                "running": ready != desired,
                "desired": desired,
                "ready": ready,
                "updated": status.get("updatedReplicas", 0),
                "available": status.get("availableReplicas", 0),
            }
        obj = self.get_object(res, name, namespace) or {}
        status = obj.get("status", {})
        spec = obj.get("spec", {})
        desired = spec.get("replicas", 0)
        generation = object_meta(obj).get("generation", 0)
        observed = status.get("observedGeneration", 0)
        ready = status.get("readyReplicas", 0)
        return {
            "running": ready != desired or generation != observed,
            "desired": desired,
            "ready": ready,
            "updated": status.get("updatedReplicas", 0),
            "available": status.get("availableReplicas", 0),
        }

    # ------------------------------------------------------------------
    # Pod 相关
    # ------------------------------------------------------------------
    def pod_logs(self, name: str, namespace: str, container: str | None = None, tail: int = 200):
        if self.mode != "real":
            return mock.pod_logs(name, namespace, container, tail)
        api = k8s_client.CoreV1Api()
        try:
            return api.read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                container=container,
                tail_lines=tail,
                _request_timeout=config.REQUEST_TIMEOUT,
            )
        except ApiException as exc:
            raise self._wrap(exc)

    def pods_of_workload(self, res: str, name: str, namespace: str):
        if self.mode != "real":
            return mock.pods_of_workload(res, name, namespace)
        spec = get_spec(res)
        if spec is None:
            raise ClusterError(f"不支持的资源类型：{res}", status=400)
        all_pods = self.list_objects("pods", namespace)
        picked = []
        for pod in all_pods:
            for ref in object_meta(pod).get("ownerReferences", []) or []:
                if ref.get("name") == name and ref.get("kind") == spec.kind:
                    picked.append(pod)
                    break
            else:
                # Deployment 的 Pod 归属于 ReplicaSet，需要再往上找一层
                for ref in object_meta(pod).get("ownerReferences", []) or []:
                    if ref.get("kind") == "ReplicaSet" and ref.get("name", "").startswith(name + "-"):
                        picked.append(pod)
                        break
        return picked

    # ------------------------------------------------------------------
    # 节点
    # ------------------------------------------------------------------
    def node_action(self, name: str, action: str):
        if action not in ("cordon", "uncordon"):
            raise ClusterError("仅支持 cordon / uncordon", status=400)
        if self.mode != "real":
            result = mock.node_action(name, action)
            if result is None:
                raise ClusterError("未找到该节点", status=404)
            return result
        return self.patch_object("nodes", name, {"spec": {"unschedulable": action == "cordon"}})

    # ------------------------------------------------------------------
    # 用量指标（需要 metrics-server，失败时静默降级）
    # ------------------------------------------------------------------
    def top_pods(self, namespace: str | None = None):
        if self.mode != "real":
            return mock.list_objects("pods", namespace)
        try:
            api = k8s_client.CustomObjectsApi()
            if namespace:
                data = api.list_namespaced_custom_object(
                    "metrics.k8s.io", "v1beta1", namespace, "pods", _request_timeout=10
                )
            else:
                data = api.list_cluster_custom_object(
                    "metrics.k8s.io", "v1beta1", "pods", _request_timeout=10
                )
            return data.get("items", [])
        except Exception as exc:
            logger.debug("metrics-server 不可用：%s", exc)
            return []

    # ------------------------------------------------------------------
    def _wrap(self, exc: ApiException) -> ClusterError:
        detail = ""
        try:
            body = exc.body
            if isinstance(body, str) and body:
                import json

                detail = json.loads(body).get("message", body)
            elif isinstance(body, dict):
                detail = body.get("message", "")
        except Exception:
            detail = str(exc)
        return ClusterError(detail or exc.reason or "集群请求失败", status=exc.status or 500, reason=exc.reason or "")


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


cluster = ClusterClient()
