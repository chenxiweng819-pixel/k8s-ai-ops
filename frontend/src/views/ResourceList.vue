<template>
  <div>
    <div class="page-header">
      <span class="page-title">{{ title }}</span>
      <el-tag size="small" effect="plain" type="info">{{ kind }}</el-tag>
      <el-tag size="small" effect="plain">{{ filtered.length }} 项</el-tag>
      <el-tag v-if="abnormalCount" size="small" type="danger" effect="plain">
        {{ abnormalCount }} 项异常
      </el-tag>

      <div class="page-actions">
        <el-input
          v-model="keyword"
          size="small"
          placeholder="按名称 / 命名空间过滤"
          clearable
          style="width: 220px"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button size="small" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button size="small" type="primary" @click="yamlVisible = true" :disabled="!canYaml">
          YAML
        </el-button>
      </div>
    </div>

    <!-- 资源的特殊提示 -->
    <el-alert
      v-if="res === 'secrets'"
      type="info"
      :closable="false"
      show-icon
      title="密钥内容已做脱敏展示，仅显示键名。查看完整内容请点击 YAML（需谨慎，避免泄露）。"
      style="margin-bottom: 12px"
    />

    <el-card shadow="never">
      <el-table
        :data="filtered"
        size="small"
        border
        :row-class-name="rowClass"
        @row-click="onRowClick"
        empty-text="没有找到资源"
        style="width: 100%"
      >
        <el-table-column
          v-for="col in columns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth"
          :sortable="col.sortable"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span v-html="renderCell(col, row)"></span>
          </template>
        </el-table-column>

        <el-table-column label="操作" :width="actionsWidth" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="res === 'pods'"
              size="small" text type="primary" @click.stop="openLogs(row)"
            >日志</el-button>

            <el-button
              v-if="hasIssues(row)"
              size="small" text type="danger" @click.stop="diagnose(row)"
            >诊断</el-button>

            <el-button
              v-if="scalable"
              size="small" text type="primary" @click.stop="openScale(row)"
            >伸缩</el-button>

            <el-button
              v-if="restartable"
              size="small" text type="warning" @click.stop="doRestart(row)"
            >重启</el-button>

            <el-button
              v-if="res === 'nodes'"
              size="small" text type="warning" @click.stop="nodeAction(row)"
            >{{ row._unschedulable ? "恢复调度" : "禁止调度" }}</el-button>

            <el-button
              v-if="deletable"
              size="small" text type="danger" @click.stop="doDelete(row)"
            >删除</el-button>

            <el-button size="small" text @click.stop="openYaml(row)">YAML</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <YamlDialog
      v-model="yamlVisible"
      :res="res"
      :name="current?.name"
      :namespace="current?.namespace"
      @applied="load"
    />

    <PodLogsDrawer
      v-model="logsVisible"
      :namespace="current?.namespace"
      :pod="current?.name"
    />
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { Search, Refresh } from "@element-plus/icons-vue";
import { listResources, deleteResource, restartResource, scaleResource, nodeAction as nodeActionApi } from "@/api";
import { useClusterStore } from "@/stores/cluster";
import YamlDialog from "@/components/YamlDialog.vue";
import PodLogsDrawer from "@/components/PodLogsDrawer.vue";

const props = defineProps({ resOverride: { type: String, default: "" } });

const route = useRoute();
const cluster = useClusterStore();
const openDiagnose = inject("openDiagnose");

const res = computed(() => props.resOverride || route.meta.res);
const title = computed(() => route.meta.title || res.value);
const kind = computed(() => res.value);

const items = ref([]);
const loading = ref(false);
const keyword = ref("");
const yamlVisible = ref(false);
const logsVisible = ref(false);
const current = ref(null);

const scalable = computed(() => ["deployments", "statefulsets", "replicasets"].includes(res.value));
const restartable = computed(() => ["deployments", "statefulsets", "daemonsets"].includes(res.value));
const deletable = computed(() => !["events", "endpoints"].includes(res.value));
const canYaml = computed(() => !!current.value);
const actionsWidth = computed(() => (res.value === "pods" ? 250 : 230));

// 不同资源的列定义。前端只负责展示，判异常的规则全在后端，避免两边不一致。
// 注意：这些常量必须定义在 COLUMN_MAP 之前，否则 const 的暂时性死区会直接抛错。
function WORKLOAD_COLUMNS() {
  return [
    { prop: "name", label: "名称", minWidth: 190 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "ready", label: "就绪", width: 90 },
    { prop: "image", label: "镜像", minWidth: 210 },
    { prop: "issues", label: "异常", width: 200 },
    { prop: "age", label: "创建时间", width: 180 },
  ];
}

const KEY_COLUMNS = [
  { prop: "name", label: "名称", minWidth: 200 },
  { prop: "namespace", label: "命名空间", width: 120 },
  { prop: "keys", label: "数据键", minWidth: 300 },
  { prop: "age", label: "创建时间", width: 180 },
];
const ROLE_COLUMNS = [
  { prop: "name", label: "名称", minWidth: 220 },
  { prop: "namespace", label: "命名空间", width: 130 },
  { prop: "rules", label: "规则数", width: 100 },
  { prop: "age", label: "创建时间", width: 190 },
];
const BINDING_COLUMNS = [
  { prop: "name", label: "名称", minWidth: 220 },
  { prop: "namespace", label: "命名空间", width: 130 },
  { prop: "roleRef", label: "引用角色", minWidth: 200 },
  { prop: "subjects", label: "主体", minWidth: 220 },
];

const COLUMN_MAP = {
  pods: [
    { prop: "name", label: "名称", minWidth: 210 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "phase", label: "状态", width: 100, badge: "phase" },
    { prop: "ready", label: "就绪", width: 80 },
    { prop: "restarts", label: "重启", width: 70 },
    { prop: "node", label: "节点", width: 130 },
    { prop: "image", label: "镜像", minWidth: 190 },
    { prop: "age", label: "存活", width: 90 },
  ],
  nodes: [
    { prop: "name", label: "名称", minWidth: 160 },
    { prop: "status", label: "状态", width: 110, badge: "node" },
    { prop: "roles", label: "角色", width: 110 },
    { prop: "ip", label: "内网 IP", width: 130 },
    { prop: "version", label: "版本", width: 110 },
    { prop: "cpu", label: "CPU", width: 90 },
    { prop: "memory", label: "内存", width: 100 },
    { prop: "podCount", label: "Pod", width: 80 },
    { prop: "age", label: "存活", width: 90 },
  ],
  namespaces: [
    { prop: "name", label: "名称", minWidth: 200 },
    { prop: "status", label: "状态", width: 110, badge: "phase" },
    { prop: "age", label: "创建时间", width: 200 },
  ],
  events: [
    { prop: "type", label: "类型", width: 90, badge: "event" },
    { prop: "reason", label: "原因", width: 160 },
    { prop: "object", label: "对象", width: 200 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "message", label: "内容", minWidth: 320 },
    { prop: "count", label: "次数", width: 70 },
    { prop: "lastTimestamp", label: "最近发生", width: 180 },
  ],
  deployments: WORKLOAD_COLUMNS("部署"),
  statefulsets: WORKLOAD_COLUMNS("副本集"),
  daemonsets: [
    { prop: "name", label: "名称", minWidth: 200 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "ready", label: "就绪", width: 90 },
    { prop: "desired", label: "期望", width: 70 },
    { prop: "image", label: "镜像", minWidth: 200 },
    { prop: "age", label: "创建时间", width: 180 },
  ],
  replicasets: WORKLOAD_COLUMNS("副本集"),
  jobs: WORKLOAD_COLUMNS("任务"),
  cronjobs: WORKLOAD_COLUMNS("定时任务"),
  horizontalpodautoscalers: [
    { prop: "name", label: "名称", minWidth: 200 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "target", label: "目标", minWidth: 180 },
    { prop: "minReplicas", label: "最小", width: 80 },
    { prop: "maxReplicas", label: "最大", width: 80 },
    { prop: "currentReplicas", label: "当前", width: 80 },
  ],
  configmaps: KEY_COLUMNS,
  secrets: KEY_COLUMNS,
  serviceaccounts: [
    { prop: "name", label: "名称", minWidth: 220 },
    { prop: "namespace", label: "命名空间", width: 130 },
    { prop: "secrets", label: "关联 Secret", width: 140 },
    { prop: "age", label: "创建时间", width: 190 },
  ],
  services: [
    { prop: "name", label: "名称", minWidth: 180 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "type", label: "类型", width: 110, badge: "svc" },
    { prop: "clusterIP", label: "ClusterIP", width: 140 },
    { prop: "ports", label: "端口", width: 170 },
    { prop: "selector", label: "选择器", minWidth: 180 },
    { prop: "age", label: "创建时间", width: 180 },
  ],
  ingresses: [
    { prop: "name", label: "名称", minWidth: 180 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "className", label: "Class", width: 110 },
    { prop: "hosts", label: "域名", minWidth: 200 },
    { prop: "paths", label: "路径", minWidth: 180 },
    { prop: "tls", label: "TLS", width: 80 },
  ],
  endpoints: [
    { prop: "name", label: "名称", minWidth: 220 },
    { prop: "namespace", label: "命名空间", width: 130 },
    { prop: "addresses", label: "可用地址", minWidth: 260 },
  ],
  networkpolicies: [
    { prop: "name", label: "名称", minWidth: 220 },
    { prop: "namespace", label: "命名空间", width: 130 },
    { prop: "podSelector", label: "Pod 选择器", minWidth: 240 },
    { prop: "types", label: "策略类型", minWidth: 200 },
  ],
  persistentvolumeclaims: [
    { prop: "name", label: "名称", minWidth: 200 },
    { prop: "namespace", label: "命名空间", width: 120 },
    { prop: "status", label: "状态", width: 100, badge: "pvc" },
    { prop: "capacity", label: "容量", width: 100 },
    { prop: "storageClass", label: "存储类", width: 130 },
    { prop: "volumeName", label: "绑定卷", minWidth: 160 },
    { prop: "age", label: "创建时间", width: 180 },
  ],
  persistentvolumes: [
    { prop: "name", label: "名称", minWidth: 190 },
    { prop: "status", label: "状态", width: 110, badge: "pvc" },
    { prop: "capacity", label: "容量", width: 100 },
    { prop: "storageClass", label: "存储类", width: 130 },
    { prop: "claim", label: "被声明", minWidth: 180 },
    { prop: "reclaim", label: "回收策略", width: 110 },
  ],
  storageclasses: [
    { prop: "name", label: "名称", minWidth: 180 },
    { prop: "provisioner", label: "Provisioner", minWidth: 260 },
    { prop: "reclaim", label: "回收策略", width: 110 },
    { prop: "bindingMode", label: "绑定模式", width: 180 },
    { prop: "default", label: "默认类", width: 90 },
  ],
  roles: ROLE_COLUMNS,
  clusterroles: ROLE_COLUMNS,
  rolebindings: BINDING_COLUMNS,
  clusterrolebindings: BINDING_COLUMNS,
};

const columns = computed(() => COLUMN_MAP[res.value] || [
  { prop: "name", label: "名称", minWidth: 220 },
  { prop: "namespace", label: "命名空间", width: 130 },
  { prop: "age", label: "创建时间", width: 200 },
]);

// 把原始 K8s 对象压平成表格行
function flatten(obj) {
  const m = obj.metadata || {};
  const s = obj.status || {};
  const spec = obj.spec || {};
  const k = obj.kind;

  const row = {
    _raw: obj,
    name: m.name,
    namespace: m.namespace || "",
    age: m.creationTimestamp,
    _issues: obj._issues || [],
    _unschedulable: spec.unschedulable,
  };

  if (k === "Pod") {
    const cs = s.containerStatuses || [];
    row.phase = s.phase;
    row.ready = `${cs.filter((c) => c.ready).length}/${cs.length}`;
    row.restarts = cs.reduce((a, c) => a + (c.restartCount || 0), 0);
    row.node = spec.nodeName;
    row.image = (spec.containers || [{}])[0].image || "";
  } else if (k === "Node") {
    const cond = (s.conditions || []).find((c) => c.type === "Ready");
    row.status = cond ? cond.status : "Unknown";
    row.roles = Object.keys(m.labels || {})
      .filter((l) => l.startsWith("node-role.kubernetes.io/"))
      .map((l) => l.split("/")[1])
      .join(",") || "worker";
    row.ip = ((s.addresses || []).find((a) => a.type === "InternalIP") || {}).address || "";
    row.version = (s.nodeInfo || {}).kubeletVersion || "";
    row.cpu = (s.allocatable || {}).cpu || "";
    row.memory = (s.allocatable || {}).memory || "";
    row.podCount = s._podCount;
  } else if (k === "Namespace") {
    row.status = s.phase;
  } else if (k === "Event") {
    row.type = obj.type;
    row.reason = obj.reason;
    row.object = (obj.involvedObject || {}).name;
    row.message = obj.message;
    row.count = obj.count;
    row.lastTimestamp = obj.lastTimestamp || obj.eventTime;
  } else if (["Deployment", "StatefulSet", "ReplicaSet", "Job", "CronJob"].includes(k)) {
    const desired = spec.replicas ?? s.desiredNumberScheduled ?? s.replicas ?? 0;
    const ready = s.readyReplicas ?? s.numberReady ?? s.succeeded ?? 0;
    row.ready = `${ready}/${desired}`;
    row.image = (((spec.template || {}).spec || {}).containers || [{}])[0].image || "";
    row.issues = (obj._issues || []).map((i) => i.type).join(", ");
  } else if (k === "DaemonSet") {
    row.ready = `${s.numberReady || 0}/${s.desiredNumberScheduled || 0}`;
    row.desired = s.desiredNumberScheduled;
    row.image = (((spec.template || {}).spec || {}).containers || [{}])[0].image || "";
  } else if (k === "HorizontalPodAutoscaler") {
    row.target = ((spec.scaleTargetRef || {}).kind || "") + " " + ((spec.scaleTargetRef || {}).name || "");
    row.minReplicas = spec.minReplicas;
    row.maxReplicas = spec.maxReplicas;
    row.currentReplicas = s.currentReplicas;
  } else if (k === "Service") {
    row.type = spec.type;
    row.clusterIP = spec.clusterIP;
    row.ports = (spec.ports || [])
      .map((p) => `${p.port}${p.nodePort ? ":" + p.nodePort : ""}/${p.protocol || "TCP"}`)
      .join(", ");
    row.selector = Object.entries(spec.selector || {}).map(([a, b]) => `${a}=${b}`).join(",") || "-";
  } else if (k === "Ingress") {
    row.className = spec.ingressClassName || "-";
    const rules = spec.rules || [];
    row.hosts = rules.map((r) => r.host || "*").join(", ") || "-";
    row.paths = rules
      .flatMap((r) => ((r.http || {}).paths || []).map((p) => p.path))
      .join(", ") || "-";
    row.tls = (spec.tls || []).length ? "是" : "否";
  } else if (k === "Endpoints") {
    row.addresses = (obj.subsets || [])
      .flatMap((sub) => (sub.addresses || []).map((a) => `${a.ip}:${(sub.ports || [{}])[0].port}`))
      .join(", ") || "（无可用地址）";
  } else if (k === "NetworkPolicy") {
    row.podSelector = Object.entries((spec.podSelector || {}).matchLabels || {})
      .map(([a, b]) => `${a}=${b}`).join(",") || "全部 Pod";
    row.types = (spec.policyTypes || []).join(", ") || "-";
  } else if (k === "PersistentVolumeClaim") {
    row.status = s.phase;
    row.capacity = ((s.capacity || {}).storage) || (((spec.resources || {}).requests || {}).storage) || "";
    row.storageClass = spec.storageClassName || "";
    row.volumeName = spec.volumeName || "";
  } else if (k === "PersistentVolume") {
    row.status = s.phase;
    row.capacity = ((spec.capacity || {}).storage) || "";
    row.storageClass = spec.storageClassName || "";
    row.claim = spec.claimRef ? `${spec.claimRef.namespace}/${spec.claimRef.name}` : "-";
    row.reclaim = spec.persistentVolumeReclaimPolicy;
  } else if (k === "StorageClass") {
    row.provisioner = obj.provisioner;
    row.reclaim = obj.reclaimPolicy;
    row.bindingMode = obj.volumeBindingMode;
    row.default = ((m.annotations || {})["storageclass.kubernetes.io/is-default-class"]) === "true" ? "是" : "否";
  } else if (k === "ConfigMap") {
    row.keys = Object.keys(obj.data || {}).join(", ") || "（空）";
  } else if (k === "Secret") {
    // 只显示键名，绝不把 base64 的值发到前端
    row.keys = Object.keys(obj.data || {}).join(", ") || "（空）";
  } else if (k === "ServiceAccount") {
    row.secrets = (obj.secrets || []).length;
  } else if (k === "Role" || k === "ClusterRole") {
    row.rules = (obj.rules || []).length;
  } else if (k === "RoleBinding" || k === "ClusterRoleBinding") {
    row.roleRef = `${obj.roleRef?.kind}/${obj.roleRef?.name}`;
    row.subjects = (obj.subjects || []).map((x) => x.name).join(", ");
  }

  return row;
}

function renderCell(col, row) {
  const v = row[col.prop];
  if (v === undefined || v === null || v === "") return "-";
  const text = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  if (col.badge) {
    const color = badgeColor(col.badge, v, row);
    return `<span style="color:${color};font-weight:600">${text}</span>`;
  }
  if (col.prop === "name" || col.prop === "object") {
    return `<span class="clickable">${text}</span>`;
  }
  return text;
}

function badgeColor(kind, value, row) {
  const ok = {
    phase: ["Running", "Active", "Succeeded", "Bound"],
    node: ["True"],
    pvc: ["Bound", "Available"],
    event: ["Normal"],
    svc: ["ClusterIP", "NodePort", "LoadBalancer"],
  }[kind] || [];
  if (kind === "svc") return "#606266";
  return ok.includes(value) ? "#34c759" : "#f56c6c";
}

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  const rows = items.value.map(flatten);
  if (!kw) return rows;
  return rows.filter(
    (r) => (r.name || "").toLowerCase().includes(kw) || (r.namespace || "").toLowerCase().includes(kw)
  );
});

const abnormalCount = computed(() => items.value.filter((i) => (i._issues || []).length).length);

function hasIssues(row) {
  return (row._issues || []).length > 0;
}

function rowClass({ row }) {
  const issues = row._issues || [];
  if (!issues.length) return "";
  return issues.some((i) => i.severity === "critical") ? "row-abnormal" : "row-warning";
}

function onRowClick(row) {
  if (res.value === "pods") openLogs(row);
  else openYaml(row);
}

function openLogs(row) {
  current.value = row;
  logsVisible.value = true;
}

function openYaml(row) {
  current.value = row;
  yamlVisible.value = true;
}

function diagnose(row) {
  openDiagnose({ res: res.value, name: row.name, namespace: row.namespace, kind: kind.value });
}

async function load() {
  loading.value = true;
  try {
    const data = await listResources(res.value, { namespace: cluster.namespace || undefined });
    items.value = data.items || [];
  } catch (e) {
    items.value = [];
  } finally {
    loading.value = false;
  }
}

async function doRestart(row) {
  try {
    await ElMessageBox.confirm(
      `将滚动重启 ${row.namespace}/${row.name}，期间 Pod 会被逐个替换，可能出现短暂不可用。确认？`,
      "确认重启",
      { type: "warning", confirmButtonText: "确认重启", cancelButtonText: "取消" }
    );
  } catch (e) { return; }
  await restartResource(res.value, row.name, row.namespace);
  ElMessage.success("已触发滚动重启");
  load();
}

async function openScale(row) {
  try {
    const { value } = await ElMessageBox.prompt("请输入新的副本数（0-200）", `伸缩 ${row.name}`, {
      inputPattern: /^\d+$/,
      inputErrorMessage: "请输入非负整数",
      inputValue: String((row._raw?.spec?.replicas) ?? 1),
      confirmButtonText: "确认",
      cancelButtonText: "取消",
    });
    await scaleResource(res.value, row.name, row.namespace, Number(value));
    ElMessage.success(`副本数已调整为 ${value}`);
    load();
  } catch (e) { /* 取消 */ }
}

async function doDelete(row) {
  const force = res.value === "pods";
  try {
    await ElMessageBox.confirm(
      `确认删除 ${res.value}/${row.namespace ? row.namespace + "/" : ""}${row.name}？` +
        (force ? " 若该 Pod 由控制器管理会自动重建。" : " 此操作不可撤销。"),
      "确认删除",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" }
    );
  } catch (e) { return; }
  await deleteResource(res.value, row.name, row.namespace);
  ElMessage.success("已删除");
  load();
}

async function nodeAction(row) {
  const action = row._unschedulable ? "uncordon" : "cordon";
  try {
    await ElMessageBox.confirm(
      action === "cordon"
        ? `将节点 ${row.name} 标记为不可调度，现有 Pod 不受影响，新 Pod 不会被调度到该节点。确认？`
        : `将恢复节点 ${row.name} 的调度能力。确认？`,
      "确认操作",
      { type: "warning", confirmButtonText: "确认", cancelButtonText: "取消" }
    );
  } catch (e) { return; }
  await nodeActionApi(row.name, action);
  ElMessage.success(action === "cordon" ? "节点已禁止调度" : "节点已恢复调度");
  load();
}

watch(() => [res.value, cluster.namespace], load);
watch(res, () => { current.value = null; keyword.value = ""; });

onMounted(load);
</script>

<style scoped>
:deep(.clickable) { color: var(--el-color-primary); cursor: pointer; }
:deep(.clickable:hover) { text-decoration: underline; }
</style>
