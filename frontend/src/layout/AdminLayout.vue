<template>
  <el-container class="layout">
    <!-- 侧边栏 -->
    <el-aside width="228px" class="aside">
      <div class="brand">
        <div class="logo">K8s</div>
        <div class="brand-text">
          <div class="brand-title">K8s 管理平台</div>
          <div class="brand-sub">{{ cluster.state.context || "未连接" }}</div>
        </div>
      </div>

      <!-- 命名空间选择（Kuboard 风格放在左侧顶部） -->
      <div class="ns-picker">
        <el-select
          v-model="nsModel"
          placeholder="全部命名空间"
          size="small"
          filterable
          clearable
          :teleported="false"
        >
          <el-option label="全部命名空间" value="" />
          <el-option v-for="n in cluster.namespaces" :key="n" :label="n" :value="n" />
        </el-select>
      </div>

      <el-scrollbar class="menu-scroll">
        <el-menu :default-active="$route.path" router class="side-menu" :collapse="false">
          <el-menu-item index="/overview">
            <el-icon><Odometer /></el-icon><span>集群总览</span>
          </el-menu-item>

          <el-menu-item index="/diagnostics">
            <el-icon><Warning /></el-icon>
            <span>异常巡检</span>
            <el-badge
              v-if="diagnosticCount"
              :value="diagnosticCount"
              :type="diagnosticCritical ? 'danger' : 'warning'"
              class="menu-badge"
            />
          </el-menu-item>

          <el-menu-item index="/assistant">
            <el-icon><MagicStick /></el-icon>
            <span>AI 运维助手</span>
            <el-tag v-if="cluster.ai.online" size="small" type="success" effect="dark" class="menu-tag">
              AI
            </el-tag>
          </el-menu-item>

          <el-sub-menu v-for="g in cluster.menuGroups" :key="g.key" :index="g.key">
            <template #title>
              <el-icon><component :is="groupIcon(g.key)" /></el-icon>
              <span>{{ g.label }}</span>
            </template>
            <el-menu-item v-for="item in g.items" :key="item.key" :index="pathOf(item)">
              {{ item.label }}
            </el-menu-item>
          </el-sub-menu>

          <el-menu-item index="/settings">
            <el-icon><Setting /></el-icon><span>集群接入</span>
          </el-menu-item>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container>
      <!-- 演示模式横幅 -->
      <div v-if="cluster.isDemo" class="demo-banner">
        <el-icon><InfoFilled /></el-icon>
        <span>
          当前为<strong>演示模式</strong>（未连接真实集群）：{{ cluster.state.error || "未找到 kubeconfig" }}
        </span>
        <el-button size="small" text type="primary" @click="$router.push('/settings')">
          去接入集群
        </el-button>
      </div>

      <el-header height="52px" class="topbar">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item>集群</el-breadcrumb-item>
          <el-breadcrumb-item>{{ $route.meta.title || "总览" }}</el-breadcrumb-item>
        </el-breadcrumb>

        <div class="topbar-right">
          <el-tag :type="cluster.isDemo ? 'warning' : 'success'" size="small" effect="plain">
            {{ cluster.isDemo ? "演示集群" : "已连接" }}
          </el-tag>
          <el-tag v-if="cluster.state.context" size="small" effect="plain" type="info">
            {{ cluster.state.context }}
          </el-tag>
          <el-button size="small" text @click="refreshAll">
            <el-icon><Refresh /></el-icon>&nbsp;刷新
          </el-button>
          <el-button size="small" text type="primary" @click="$router.push('/assistant')">
            <el-icon><MagicStick /></el-icon>&nbsp;问 AI
          </el-button>
        </div>
      </el-header>

      <el-main class="main">
        <router-view v-slot="{ Component }">
          <keep-alive :max="6">
            <component :is="Component" :key="$route.fullPath" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>

    <!-- 一键诊断结果抽屉，全局共用 -->
    <DiagnoseDrawer v-model="diagnose.visible" :target="diagnose.target" />
  </el-container>
</template>

<script setup>
import { computed, onMounted, provide, reactive, ref } from "vue";
import { useClusterStore } from "@/stores/cluster";
import { getDiagnostics } from "@/api";
import DiagnoseDrawer from "@/components/DiagnoseDrawer.vue";
import {
  Odometer, Warning, MagicStick, Setting, Refresh, InfoFilled,
  Monitor, Files, Bell, Box, Grid, Coin, SetUp, Tickets, Clock,
  TrendCharts, Document, Key, Avatar, Connection, Guide, Link,
  Lock, FolderOpened, Collection, UserFilled, User, Cpu,
} from "@element-plus/icons-vue";

const cluster = useClusterStore();
const diagnosticCount = ref(0);
const diagnosticCritical = ref(false);

const diagnose = reactive({ visible: false, target: null });

// 任意子页面都能调用 openDiagnose({res,name,namespace}) 弹出诊断抽屉
function openDiagnose(target) {
  diagnose.target = target;
  diagnose.visible = true;
}
provide("openDiagnose", openDiagnose);

const nsModel = computed({
  get: () => cluster.namespace,
  set: (v) => cluster.setNamespace(v),
});

const ICONS = {
  Monitor, Files, Bell, Box, Grid, Coin, SetUp, Tickets, Clock,
  TrendCharts, Document, Key, Avatar, Connection, Guide, Link,
  Lock, FolderOpened, Collection, UserFilled, User,
};
const GROUP_ICONS = {
  cluster: Cpu, workload: Box, config: Document,
  network: Connection, storage: Collection, rbac: Lock,
};
const groupIcon = (g) => GROUP_ICONS[g] || Files;

// 把注册表的 key 映射到路由 path
function pathOf(item) {
  const map = {
    nodes: "/cluster/nodes", namespaces: "/cluster/namespaces", events: "/cluster/events",
    pods: "/workloads/pods", deployments: "/workloads/deployments",
    statefulsets: "/workloads/statefulsets", daemonsets: "/workloads/daemonsets",
    jobs: "/workloads/jobs", cronjobs: "/workloads/cronjobs",
    horizontalpodautoscalers: "/workloads/hpa",
    configmaps: "/config/configmaps", secrets: "/config/secrets",
    serviceaccounts: "/config/serviceaccounts",
    services: "/network/services", ingresses: "/network/ingresses",
    endpoints: "/network/endpoints", networkpolicies: "/network/networkpolicies",
    persistentvolumeclaims: "/storage/pvc", persistentvolumes: "/storage/pv",
    storageclasses: "/storage/storageclasses",
    roles: "/rbac/roles", clusterroles: "/rbac/clusterroles",
  };
  return map[item.key] || `/workloads/${item.key}`;
}

async function loadDiagnosticCount() {
  try {
    const data = await getDiagnostics();
    diagnosticCount.value = data.summary?.total || 0;
    diagnosticCritical.value = (data.summary?.critical || 0) > 0;
  } catch (e) {
    diagnosticCount.value = 0;
  }
}

async function refreshAll() {
  await cluster.bootstrap();
  loadDiagnosticCount();
}

onMounted(async () => {
  if (!cluster.registry.resources.length) await cluster.bootstrap();
  loadDiagnosticCount();
  // 侧栏角标定期刷新，保持与集群状态同步
  setInterval(loadDiagnosticCount, 60000);
});
</script>

<style scoped>
.layout { height: 100dvh; }

.aside {
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px 10px;
}
.logo {
  width: 34px; height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #3a7afe, #6b5cff);
  color: #fff;
  font-weight: 800;
  font-size: 13px;
  display: grid; place-items: center;
  flex-shrink: 0;
}
.brand-title { color: #fff; font-size: 14px; font-weight: 650; }
.brand-sub {
  color: #7f8b9c; font-size: 11px; margin-top: 2px;
  max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ns-picker { padding: 4px 12px 10px; }
.ns-picker :deep(.el-select) { width: 100%; }

.menu-scroll { flex: 1; }

.side-menu {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--sidebar-text);
  --el-menu-hover-bg-color: #2b3648;
  --el-menu-active-color: #fff;
}
.side-menu :deep(.el-sub-menu__title) { color: var(--sidebar-text); }
.side-menu :deep(.el-menu-item.is-active) {
  background: var(--sidebar-active);
  color: #fff;
  border-radius: 6px;
  margin: 0 8px;
}
.side-menu :deep(.el-menu-item) { height: 40px; line-height: 40px; }
.menu-badge { margin-left: 8px; }
.menu-tag { margin-left: 8px; height: 18px; padding: 0 5px; }

.topbar {
  background: #fff;
  border-bottom: 1px solid #e6eaf0;
  display: flex;
  align-items: center;
  gap: 14px;
}
.topbar-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }

.main {
  background: var(--page-bg);
  padding: 16px;
  overflow-y: auto;
}
</style>