import { defineStore } from "pinia";
import * as api from "@/api";
import { aiHealth, aiKnowledge } from "@/api/ai";

export const useClusterStore = defineStore("cluster", {
  state: () => ({
    state: { connected: false, context: "", error: "", demo: false },
    mode: "demo",
    registry: { resources: [], groups: {} },
    ai: { online: false, llmConfigured: false, llmModel: "", clusterMode: "" },
    knowledge: {},
    namespace: localStorage.getItem("k8s-ns") || "",
    namespaces: [],
    loading: false,
  }),

  getters: {
    isDemo: (s) => s.mode === "demo",
    // 侧边栏菜单：由后端注册表驱动，新增资源类型无需改前端
    menuGroups: (s) => {
      const labels = {
        cluster: "集群", workload: "工作负载", config: "配置",
        network: "网络", storage: "存储", rbac: "权限",
      };
      const order = ["cluster", "workload", "config", "network", "storage", "rbac"];
      return order
        .filter((g) => s.registry.groups[g])
        .map((g) => ({ key: g, label: labels[g] || g, items: s.registry.groups[g] }));
    },
  },

  actions: {
    setNamespace(ns) {
      this.namespace = ns || "";
      localStorage.setItem("k8s-ns", this.namespace);
    },

    async refreshState() {
      try {
        const data = await api.getClusterState();
        this.state = data;
        this.mode = data.mode || "demo";
      } catch (e) {
        this.state = { connected: false, error: e.message };
      }
    },

    async refreshAi() {
      try {
        const data = await aiHealth();
        this.ai = {
          online: true,
          llmConfigured: data.llmConfigured,
          llmModel: data.llmModel,
          clusterMode: data.clusterMode,
        };
      } catch (e) {
        this.ai = { online: false, llmConfigured: false, llmModel: "", clusterMode: "" };
      }
    },

    async loadRegistry() {
      const data = await api.getRegistry();
      this.registry = data;
    },

    async loadKnowledge() {
      try {
        const data = await aiKnowledge();
        this.knowledge = data.knowledge || {};
      } catch (e) {
        this.knowledge = {};
      }
    },

    async loadNamespaces() {
      try {
        const data = await api.listResources("namespaces");
        this.namespaces = (data.items || []).map((n) => n.metadata.name);
      } catch (e) {
        this.namespaces = [];
      }
    },

    async bootstrap() {
      this.loading = true;
      try {
        await Promise.all([
          this.refreshState(),
          this.loadRegistry(),
          this.refreshAi(),
          this.loadKnowledge(),
        ]);
        await this.loadNamespaces();
      } finally {
        this.loading = false;
      }
    },

    async importConfig(text) {
      const info = await api.importKubeconfig(text);
      await this.refreshState();
      return info;
    },
  },
});
