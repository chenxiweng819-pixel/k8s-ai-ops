"""K8s 访问层。

设计要点：
1. 用 kubeconfig 初始化 kubernetes 客户端的 ApiClient，复用其证书 / token /
   exec 插件等鉴权能力；
2. 不逐个封装 typed client，而是走一条**通用 REST 通道**（api/v1 与 apis/<group>/<version>），
   这样任何资源（含 CRD）都能统一 list/get/patch/delete，而无需写几十个方法；
3. 集群不可达时按配置退回演示数据，保证平台离线也能完整演示。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import yaml
from kubernetes import client, config as kube_config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from . import config
from .demo import DemoBackend
from .diagnose import pod_issues, summarize, workload_issues

from .registry import K8sError, RESOURCES, SCALABLE, RESTARTABLE, api_prefix, resolve

log = logging.getLogger("k8s")




class KubeClient:
    """基于 kubeconfig 的通用 K8s 客户端。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._api: client.ApiClient | None = None
        self._active_context = None
        self._kubeconfig_path = None
        self._last_error = None

    # ---------------- kubeconfig ----------------
    @staticmethod
    def find_kubeconfig():
        for p in config.kubeconfig_candidates():
            if p and os.path.isfile(p):
                return p
        return None

    def connect(self, path=None):
        """(重新)加载 kubeconfig 并建立 ApiClient。失败抛 K8sError。"""
        target = path or self.find_kubeconfig()
        if not target:
            raise K8sError(404, "未找到 kubeconfig，请将文件放到 D:\\config 或在界面中粘贴上传")

        try:
            contexts, active = kube_config.list_kube_config_contexts(config_file=target)
        except Exception as exc:  # noqa: BLE001
            raise K8sError(400, f"kubeconfig 解析失败: {exc}") from exc

        if not contexts:
            raise K8sError(400, "kubeconfig 中没有任何 context")

        try:
            kube_config.load_kube_config(config_file=target, context=active.get("name"))
        except Exception as exc:  # noqa: BLE001
            raise K8sError(400, f"kubeconfig 加载失败: {exc}") from exc

        with self._lock:
            self._api = client.ApiClient()
            self._api.configuration.connection_pool_maxsize = 20
            self._active_context = active.get("name")
            self._kubeconfig_path = target
            self._last_error = None

        # 连通性探测：失败时必须收回 _api，否则上层会误判为「已连接」
        try:
            self.version()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            with self._lock:
                self._api = None
            raise K8sError(502, f"无法连接集群: {exc}") from exc

        log.info("已连接集群 context=%s config=%s", self._active_context, target)
        return {"context": self._active_context, "kubeconfig": target}

    def save_uploaded_kubeconfig(self, text):
        """保存界面粘贴的 kubeconfig，并立即尝试连接。"""
        try:
            yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            raise K8sError(400, f"内容不是合法 YAML: {exc}") from exc

        path = os.path.join(config.PROJECT_DIR, "uploaded-kubeconfig.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.connect(path)

    @property
    def connected(self):
        return self._api is not None

    def state(self):
        return {
            "connected": self.connected,
            "context": self._active_context,
            "kubeconfig": self._kubeconfig_path,
            "error": self._last_error,
        }

    # ---------------- 通用 REST ----------------
    def _client(self):
        if self._api is None:
            try:
                self.connect()
            except K8sError:
                raise
        return self._api

    def request(self, method, path, query=None, body=None, timeout=60, raw=False):
        """对 K8s API Server 发起原始请求。"""
        api = self._client()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = api.configuration.api_key.get("authorization")
        if token:
            headers["Authorization"] = token

        kwargs = {
            "query_params": query or [],
            "header_params": headers,
            "auth_settings": ["BearerToken"],
            "_preload_content": False,
            "_request_timeout": timeout,
        }
        if body is not None:
            kwargs["body"] = body

        try:
            resp = api.call_api(path, method, **kwargs)
            text = resp.data.decode("utf-8", errors="replace")
            status = resp.status
        except ApiException as exc:
            # typed client 的异常路径
            detail = _clean_api_error(exc.body) or str(exc.reason or exc)
            raise K8sError(exc.status or 500, detail) from exc
        except Exception as exc:  # noqa: BLE001
            raise K8sError(502, f"请求 K8s API 失败: {exc}") from exc

        if status >= 400:
            raise K8sError(status, _clean_api_error(text))
        if raw:
            return text
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise K8sError(502, "K8s API 返回了非 JSON 内容") from exc

    # ---------------- 便捷方法 ----------------
    def version(self):
        return self.request("GET", "/version")

    def api_resources(self):
        """集群支持的资源类型（用于「发现 CRD」等高级功能）。"""
        core = self.request("GET", "/api/v1")
        groups = self.request("GET", "/apis")
        return {"core": core, "groups": groups}

    def collection_path(self, meta, namespace=None):
        base = meta["api"]
        if meta["namespaced"]:
            ns = namespace or "default"
            return f"{base}/namespaces/{ns}/{meta['name']}"
        return f"{base}/{meta['name']}"

    def item_path(self, meta, name, namespace=None):
        return f"{self.collection_path(meta, namespace)}/{name}"

    def list_resource(self, key, namespace=None, label_selector=None, field_selector=None, limit=None):
        rkey, meta = resolve(key)
        query = []
        if label_selector:
            query.append(("labelSelector", label_selector))
        if field_selector:
            query.append(("fieldSelector", field_selector))
        if limit:
            query.append(("limit", limit))

        if meta["namespaced"] and not namespace:
            # 跨命名空间拉取：/api/v1/pods
            path = f"{meta['api']}/{meta['name']}"
        else:
            path = self.collection_path(meta, namespace)

        data = self.request("GET", path, query=query)
        items = data.get("items", [])
        return {"resource": rkey, "kind": meta["kind"], "items": items, "count": len(items)}

    def get_resource(self, key, name, namespace=None):
        rkey, meta = resolve(key)
        return self.request("GET", self.item_path(meta, name, namespace))

    def delete_resource(self, key, name, namespace=None, grace=None):
        rkey, meta = resolve(key)
        query = []
        if grace is not None:
            query.append(("gracePeriodSeconds", int(grace)))
        body = {"apiVersion": "v1", "kind": "DeleteOptions"}
        if grace is not None:
            body["gracePeriodSeconds"] = int(grace)
        return self.request("DELETE", self.item_path(meta, name, namespace), query=query, body=body)

    def patch_resource(self, key, name, namespace, patch, patch_type="merge"):
        rkey, meta = resolve(key)
        content_type = {
            "merge": "application/merge-patch+json",
            "strategic": "application/strategic-merge-patch+json",
            "json": "application/json-patch+json",
        }[patch_type]
        api = self._client()
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "Authorization": api.configuration.api_key.get("authorization", ""),
        }
        path = self.item_path(meta, name, namespace)
        try:
            resp = api.call_api(
                path, "PATCH",
                header_params=headers,
                auth_settings=["BearerToken"],
                body=patch,
                _preload_content=False,
                _request_timeout=60,
            )
            text = resp.data.decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise K8sError(resp.status, _clean_api_error(text))
            return json.loads(text) if text.strip() else {}
        except K8sError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise K8sError(502, f"patch 失败: {exc}") from exc

    def create_resource(self, manifest):
        """按 manifest 自身推断路径并创建。"""
        kind = manifest.get("kind")
        api_version = manifest.get("apiVersion", "v1")
        key = None
        for name, meta in RESOURCES.items():
            if meta["kind"] == kind:
                key = name
                break
        if key is None:
            raise K8sError(400, f"不支持的 kind: {kind}")

        _, meta = resolve(key)
        ns = (manifest.get("metadata") or {}).get("namespace") if meta["namespaced"] else None
        if meta["api"] != api_prefix(api_version):
            # manifest 自带的 apiVersion 与注册表不一致时以 manifest 为准
            meta = dict(meta, api=api_prefix(api_version))
        path = self.collection_path(meta, ns)
        return self.request("POST", path, body=manifest)

    def replace_resource(self, manifest):
        kind = manifest.get("kind")
        name = (manifest.get("metadata") or {}).get("name")
        ns = (manifest.get("metadata") or {}).get("namespace")
        key = None
        for k, meta in RESOURCES.items():
            if meta["kind"] == kind:
                key = k
                break
        if key is None or not name:
            raise K8sError(400, "manifest 缺少可识别的 kind 或 metadata.name")
        _, meta = resolve(key)
        meta = dict(meta, api=api_prefix(manifest.get("apiVersion", "v1")))
        return self.request("PUT", self.item_path(meta, name, ns), body=manifest)

    def scale(self, key, name, namespace, replicas):
        rkey, meta = resolve(key)
        if rkey not in SCALABLE:
            raise K8sError(400, f"{meta['kind']} 不支持扩缩容")
        patch = {"spec": {"replicas": int(replicas)}}
        return self.patch_resource(rkey, name, namespace, patch, "merge")

    def restart(self, key, name, namespace):
        """通过打 rollout 注解触发滚动重启，等价于 kubectl rollout restart。"""
        rkey, meta = resolve(key)
        if rkey not in RESTARTABLE:
            raise K8sError(400, f"{meta['kind']} 不支持重启")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                    }
                }
            }
        }
        return self.patch_resource(rkey, name, namespace, patch, "merge")

    def pod_logs(self, namespace, name, container=None, tail=200, previous=False):
        query = [("tailLines", int(tail))]
        if container:
            query.append(("container", container))
        if previous:
            query.append(("previous", "true"))
        path = f"/api/v1/namespaces/{namespace}/pods/{name}/log"
        return self.request("GET", path, query=query, timeout=30, raw=True)

    def pod_containers(self, namespace, name):
        pod = self.get_resource("pods", name, namespace)
        spec = (pod.get("spec") or {}).get("containers", [])
        statuses = (pod.get("status") or {}).get("containerStatuses", []) or []
        return [
            {
                "name": c.get("name"),
                "image": c.get("image"),
                "ready": next((s.get("ready") for s in statuses if s.get("name") == c.get("name")), None),
                "restarts": next((s.get("restartCount") for s in statuses if s.get("name") == c.get("name")), 0),
            }
            for c in spec
        ]

    def pod_exec(self, namespace, name, command, container=None):
        """一次性（非交互）在容器内执行命令。"""
        api = self._client()
        resp = stream(
            api.connect_get_namespaced_pod_exec,
            name,
            namespace,
            container=container,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        return resp

    def node_action(self, name, action):
        """节点调度操作：cordon / uncordon。"""
        if action not in ("cordon", "uncordon"):
            raise K8sError(400, f"不支持的节点操作: {action}")
        patch = {"spec": {"unschedulable": action == "cordon"}}
        return self.patch_resource("nodes", name, None, patch, "merge")

    def cluster_overview(self):
        """总览页聚合数据。"""
        nodes = self.list_resource("nodes")["items"]
        namespaces = self.list_resource("namespaces")["items"]
        pods = self.list_resource("pods")["items"]
        deployments = self.list_resource("deployments")["items"]
        services = self.list_resource("services")["items"]

        phase_count = {}
        for p in pods:
            phase = (p.get("status") or {}).get("phase", "Unknown")
            phase_count[phase] = phase_count.get(phase, 0) + 1

        ready_nodes = sum(
            1
            for n in nodes
            if any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in (n.get("status") or {}).get("conditions", [])
            )
        )

        # 异常统计：必须与 /api/diagnostics 口径一致（Pod + 节点 + 工作负载），
        # 否则总览页和侧边栏会出现两个不同的「异常对象」数字
        abnormal_pods = []
        for p in pods:
            issues = pod_issues(p)
            if not issues:
                continue
            m, s, spec = p.get("metadata") or {}, p.get("status") or {}, p.get("spec") or {}
            abnormal_pods.append({
                "kind": "Pod",
                "namespace": m.get("namespace"),
                "name": m.get("name"),
                "phase": s.get("phase"),
                "node": spec.get("nodeName"),
                "issues": issues,
                "severity": "critical" if any(i["severity"] == "critical" for i in issues) else "warning",
            })

        abnormal_nodes = summarize(nodes, "Node")

        abnormal_workloads = []
        for rkey in ("deployments", "statefulsets", "daemonsets"):
            _, meta = resolve(rkey)
            for w in self.list_resource(rkey)["items"]:
                issues = workload_issues(w)
                if not issues:
                    continue
                abnormal_workloads.append({
                    "kind": meta["kind"],
                    "resource": rkey,
                    "namespace": (w.get("metadata") or {}).get("namespace"),
                    "name": (w.get("metadata") or {}).get("name"),
                    "issues": issues,
                    "severity": "critical" if any(i["severity"] == "critical" for i in issues) else "warning",
                })

        return {
            "version": self.version(),
            "context": self._active_context,
            "counts": {
                "nodes": len(nodes),
                "readyNodes": ready_nodes,
                "namespaces": len(namespaces),
                "pods": len(pods),
                "deployments": len(deployments),
                "services": len(services),
            },
            "podPhases": phase_count,
            "abnormalPods": abnormal_pods[:50],
            "abnormalNodes": abnormal_nodes,
            "abnormalWorkloads": abnormal_workloads,
            "abnormalCount": len(abnormal_pods) + len(abnormal_nodes) + len(abnormal_workloads),
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _clean_api_error(text):
    """把 K8s 的 Status 对象压成一行可读信息。"""
    if not text:
        return "K8s API 返回空错误"
    try:
        data = json.loads(text)
        msg = data.get("message") or data.get("reason")
        if msg:
            return msg
        return json.dumps(data, ensure_ascii=False)[:500]
    except Exception:  # noqa: BLE001
        return str(text)[:500]




# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_client_singleton = None
_demo_singleton = None


def get_client():
    """返回后端实现：真实集群优先，不可达时按配置退回演示后端。

    两者接口完全一致，上层代码无需感知差异。
    """
    global _client_singleton, _demo_singleton
    if _client_singleton is None:
        _client_singleton = KubeClient()
    try:
        _client_singleton._client()
        return _client_singleton, "live"
    except K8sError as exc:
        if config.ALLOW_DEMO_FALLBACK:
            if _demo_singleton is None:
                _demo_singleton = DemoBackend()
            _demo_singleton.reason = exc.message
            return _demo_singleton, "demo"
        raise
