<template>
  <div v-loading="loading">
    <div class="page-header">
      <span class="page-title">异常巡检</span>
      <el-tag size="small" type="danger" effect="plain">严重 {{ summary.critical || 0 }}</el-tag>
      <el-tag size="small" type="warning" effect="plain">警告 {{ summary.warning || 0 }}</el-tag>
      <div class="page-actions">
        <el-select v-model="severity" size="small" style="width: 130px" @change="load">
          <el-option label="全部" value="all" />
          <el-option label="仅严重" value="critical" />
          <el-option label="仅警告" value="warning" />
        </el-select>
        <el-button size="small" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;重新巡检
        </el-button>
        <el-button size="small" type="primary" @click="askAi">
          <el-icon><MagicStick /></el-icon>&nbsp;让 AI 分析全部异常
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="!findings.length && !loading"
      type="success"
      :closable="false"
      show-icon
      title="巡检通过：当前没有检测到处于异常状态的 Pod、工作负载或节点"
      style="margin-bottom: 12px"
    />

    <!-- 按类型聚合，便于快速判断「是普遍问题还是个别问题」 -->
    <el-card v-if="grouped.length" shadow="never" style="margin-bottom: 14px">
      <template #header><span>按异常类型聚合</span></template>
      <div class="type-grid">
        <div v-for="g in grouped" :key="g.type" class="type-card" @click="filterByType(g.type)">
          <div class="type-head">
            <el-tag :type="g.critical ? 'danger' : 'warning'" size="small">{{ g.type }}</el-tag>
            <span class="count">{{ g.items.length }} 个</span>
          </div>
          <div class="type-meaning">{{ g.meaning }}</div>
        </div>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>异常明细（{{ filtered.length }}）</span>
          <el-input
            v-model="keyword"
            size="small"
            placeholder="过滤对象名"
            clearable
            style="width: 200px"
          />
        </div>
      </template>

      <el-collapse v-model="activeNames">
        <el-collapse-item
          v-for="f in filtered"
          :key="`${f.kind}-${f.namespace}-${f.name}`"
          :name="`${f.kind}-${f.namespace}-${f.name}`"
        >
          <template #title>
            <el-tag :type="f.severity === 'critical' ? 'danger' : 'warning'" size="small" style="margin-right: 8px">
              {{ f.severity === "critical" ? "严重" : "警告" }}
            </el-tag>
            <span class="obj-name">
              {{ f.kind }} · {{ f.namespace ? f.namespace + "/" : "" }}{{ f.name }}
            </span>
            <el-tag
              v-for="i in f.issues"
              :key="i.type"
              size="small"
              effect="plain"
              :type="i.severity === 'critical' ? 'danger' : 'warning'"
              style="margin-left: 6px"
            >
              {{ i.type }}
            </el-tag>
          </template>

          <div class="finding-body">
            <el-table :data="f.issues" size="small" border>
              <el-table-column prop="type" label="异常类型" width="200" />
              <el-table-column prop="reason" label="说明" min-width="280" />
              <el-table-column prop="message" label="原始信息" min-width="280" show-overflow-tooltip />
            </el-table>

            <div v-if="playbookFor(f)" class="playbook">
              <div class="pb-title">可能的原因与修复方向</div>
              <div class="pb-block"><b>常见根因</b>
                <ul><li v-for="c in playbookFor(f).causes" :key="c">{{ c }}</li></ul>
              </div>
              <div class="pb-block"><b>排查命令</b>
                <ul><li v-for="c in playbookFor(f).checks" :key="c"><code class="mono">{{ c }}</code></li></ul>
              </div>
              <div class="pb-block"><b>修复方案</b>
                <ul><li v-for="c in playbookFor(f).fixes" :key="c">{{ c }}</li></ul>
              </div>
            </div>

            <div class="finding-actions">
              <el-button size="small" type="danger" @click="diagnose(f)">AI 深度诊断</el-button>
              <el-button size="small" @click="gotoResource(f)">查看资源</el-button>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Refresh, MagicStick } from "@element-plus/icons-vue";
import { getDiagnostics } from "@/api";
import { useClusterStore } from "@/stores/cluster";

const router = useRouter();
const cluster = useClusterStore();
const openDiagnose = inject("openDiagnose");

// 知识库来自 AI 服务，与 AI 诊断共用同一份，保证界面与 AI 结论一致
const PLAYBOOK = computed(() => cluster.knowledge || {});

const loading = ref(false);
const severity = ref("all");
const keyword = ref("");
const findings = ref([]);
const summary = ref({});
const activeNames = ref([]);

const RES_OF_KIND = {
  Pod: "pods", Node: "nodes", Deployment: "deployments",
  StatefulSet: "statefulsets", DaemonSet: "daemonsets",
};

const grouped = computed(() => {
  const map = {};
  for (const f of findings.value) {
    for (const i of f.issues) {
      if (!map[i.type]) map[i.type] = { type: i.type, items: [], critical: false };
      map[i.type].items.push(f);
      if (i.severity === "critical") map[i.type].critical = true;
    }
  }
  return Object.values(map).sort((a, b) => b.items.length - a.items.length).map((g) => ({
    ...g,
    meaning: (PLAYBOOK.value[g.type] || {}).meaning || "暂无预置说明",
  }));
});

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return findings.value;
  return findings.value.filter((f) => (f.name || "").toLowerCase().includes(kw));
});

// 从预置知识库取该异常的排查建议（与 AI 用的是同一份知识，保证结论一致）
function playbookFor(f) {
  for (const i of f.issues) {
    if (PLAYBOOK.value[i.type]) return PLAYBOOK.value[i.type];
  }
  return null;
}

function filterByType(type) {
  keyword.value = "";
  activeNames.value = findings.value
    .filter((f) => f.issues.find((i) => i.type === type))
    .map((f) => `${f.kind}-${f.namespace}-${f.name}`);
}

function diagnose(f) {
  openDiagnose({
    res: RES_OF_KIND[f.kind] || "pods",
    name: f.name,
    namespace: f.namespace,
    kind: f.kind,
  });
}

function gotoResource(f) {
  const res = RES_OF_KIND[f.kind];
  if (res) router.push(`/${f.kind === "Node" ? "cluster" : "workloads"}/${res}`);
}

function askAi() {
  const types = grouped.value.map((g) => `${g.type}(${g.items.length})`).join("、");
  router.push({ path: "/assistant", query: { q: `当前集群检测到这些异常：${types}。请逐个分析根因、影响范围，并给出修复优先级排序和具体操作步骤。` } });
}

async function load() {
  loading.value = true;
  try {
    const data = await getDiagnostics();
    const all = [
      ...(data.nodes || []),
      ...(data.workloads || []),
      ...(data.pods || []),
    ];
    findings.value = severity.value === "all"
      ? all
      : all.filter((f) => f.severity === severity.value);
    summary.value = data.summary || {};
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.type-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.type-card {
  border: 1px solid #e8ecf2;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.type-card:hover { border-color: var(--el-color-primary); background: #f7faff; }
.type-head { display: flex; align-items: center; justify-content: space-between; }
.count { color: #8b95a6; font-size: 12px; }
.type-meaning {
  color: #6b7280; font-size: 12.5px; margin-top: 8px; line-height: 1.55;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.card-head { display: flex; align-items: center; justify-content: space-between; }
.obj-name { font-weight: 600; }
.finding-body { padding: 4px 0; }
.finding-actions { margin-top: 12px; display: flex; gap: 10px; }
.playbook {
  margin-top: 12px;
  background: #f8fafc;
  border: 1px solid #e8ecf2;
  border-radius: 8px;
  padding: 12px 14px;
}
.pb-title { font-weight: 650; margin-bottom: 8px; }
.pb-block { margin-bottom: 8px; font-size: 13px; }
.pb-block ul { margin: 4px 0 0; padding-left: 20px; line-height: 1.75; }
.mono { background: #eef2f7; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>