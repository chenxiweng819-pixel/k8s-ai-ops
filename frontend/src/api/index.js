import http from "@/utils/request";

// ---------------- 集群 ----------------
export const getHealth = () => http.get("/health");
export const getClusterState = () => http.get("/cluster/state");
export const getOverview = () => http.get("/cluster/overview");
export const getRegistry = () => http.get("/cluster/registry");
export const importKubeconfig = (kubeconfig) => http.post("/cluster/kubeconfig", { kubeconfig });

// ---------------- 通用资源 ----------------
export const listResources = (res, params) => http.get(`/resources/${res}`, { params });
export const getResource = (res, name, namespace) =>
  http.get(`/resources/${res}/${name}`, { params: { namespace } });
export const getResourceYaml = (res, name, namespace) =>
  http.get(`/resources/${res}/${name}/yaml`, { params: { namespace } });

export const deleteResource = (res, name, namespace) =>
  http.delete(`/resources/${res}/${name}`, { params: { namespace }, data: { confirm: true } });

export const applyYaml = (yaml) => http.post("/resources/apply", { yaml, confirm: true });

export const scaleResource = (res, name, namespace, replicas) =>
  http.post(`/resources/${res}/${name}/scale`, { namespace, replicas, confirm: true });

export const restartResource = (res, name, namespace) =>
  http.post(`/resources/${res}/${name}/restart`, { namespace, confirm: true });

// ---------------- Pod ----------------
export const getPodContainers = (ns, name) => http.get(`/pods/${ns}/${name}/containers`);
export const getPodLogs = (ns, name, params) => http.get(`/pods/${ns}/${name}/logs`, { params });
export const execInPod = (ns, name, command, container) =>
  http.post(`/pods/${ns}/${name}/exec`, { command, container, confirm: true });

// ---------------- 节点 ----------------
export const nodeAction = (name, action) => http.post(`/nodes/${name}/${action}`, { confirm: true });

// ---------------- 诊断 / 审计 ----------------
export const getDiagnostics = (namespace) => http.get("/diagnostics", { params: { namespace } });
export const getAudit = (limit = 100) => http.get("/audit", { params: { limit } });
