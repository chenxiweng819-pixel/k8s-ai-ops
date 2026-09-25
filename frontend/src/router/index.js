import { createRouter, createWebHistory } from "vue-router";
import AdminLayout from "@/layout/AdminLayout.vue";

// 用一份声明式配置生成路由：资源类页面全部复用 ResourceList，
// 由 meta.res 决定渲染哪种资源，避免为每个 kind 写一个页面。
const RESOURCE_ROUTES = [
  // 集群
  { path: "cluster/nodes", res: "nodes", title: "节点", icon: "Monitor" },
  { path: "cluster/namespaces", res: "namespaces", title: "命名空间", icon: "Files" },
  { path: "cluster/events", res: "events", title: "事件", icon: "Bell" },
  // 工作负载
  { path: "workloads/pods", res: "pods", title: "容器组", icon: "Box" },
  { path: "workloads/deployments", res: "deployments", title: "部署", icon: "Grid" },
  { path: "workloads/statefulsets", res: "statefulsets", title: "有状态副本集", icon: "Coin" },
  { path: "workloads/daemonsets", res: "daemonsets", title: "守护进程集", icon: "SetUp" },
  { path: "workloads/jobs", res: "jobs", title: "任务", icon: "Tickets" },
  { path: "workloads/cronjobs", res: "cronjobs", title: "定时任务", icon: "Clock" },
  { path: "workloads/hpa", res: "horizontalpodautoscalers", title: "弹性伸缩", icon: "TrendCharts" },
  // 配置
  { path: "config/configmaps", res: "configmaps", title: "配置字典", icon: "Document" },
  { path: "config/secrets", res: "secrets", title: "密钥", icon: "Key" },
  { path: "config/serviceaccounts", res: "serviceaccounts", title: "服务账户", icon: "Avatar" },
  // 网络
  { path: "network/services", res: "services", title: "服务", icon: "Connection" },
  { path: "network/ingresses", res: "ingresses", title: "路由", icon: "Guide" },
  { path: "network/endpoints", res: "endpoints", title: "端点", icon: "Link" },
  { path: "network/networkpolicies", res: "networkpolicies", title: "网络策略", icon: "Lock" },
  // 存储
  { path: "storage/pvc", res: "persistentvolumeclaims", title: "存储声明", icon: "FolderOpened" },
  { path: "storage/pv", res: "persistentvolumes", title: "持久卷", icon: "Coin" },
  { path: "storage/storageclasses", res: "storageclasses", title: "存储类", icon: "Collection" },
  // 权限
  { path: "rbac/roles", res: "roles", title: "角色", icon: "UserFilled" },
  { path: "rbac/clusterroles", res: "clusterroles", title: "集群角色", icon: "User" },
];

const routes = [
  {
    path: "/",
    component: AdminLayout,
    redirect: "/overview",
    children: [
      {
        path: "overview",
        name: "overview",
        component: () => import("@/views/Overview.vue"),
        meta: { title: "集群总览" },
      },
      {
        path: "diagnostics",
        name: "diagnostics",
        component: () => import("@/views/Diagnostics.vue"),
        meta: { title: "异常巡检" },
      },
      {
        path: "assistant",
        name: "assistant",
        component: () => import("@/views/Assistant.vue"),
        meta: { title: "AI 运维助手" },
      },
      {
        path: "settings",
        name: "settings",
        component: () => import("@/views/Settings.vue"),
        meta: { title: "集群接入" },
      },
      ...RESOURCE_ROUTES.map((r) => ({
        path: r.path,
        name: r.res,
        component: () => import("@/views/ResourceList.vue"),
        meta: { title: r.title, icon: r.icon, res: r.res },
      })),
    ],
  },
  { path: "/:pathMatch(.*)*", redirect: "/overview" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.afterEach((to) => {
  document.title = (to.meta?.title ? to.meta.title + " · " : "") + "K8s 管理平台";
});

export default router;
export { RESOURCE_ROUTES };
