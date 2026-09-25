<template>
  <div v-loading="loading">
    <div class="page-header">
      <span class="page-title">集群总览</span>
      <el-tag v-if="data.version" size="small" effect="plain">
        Kubernetes {{ data.version.gitVersion }}
      </el-tag>
      <el-tag size="small" effect="plain" type="info">{{ data.context }}</el-tag>
      <div class="page-actions">
        <el-button size="small" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button size="small" type="primary" @click="$router.push('/diagnostics')">
          异常巡检
        </el-button>
      </div>
    </div>

    <!-- 关键指标 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="label">节点</div>
        <div class="value">
          {{ data.counts?.readyNodes }}<span class="sub"> / {{ data.counts?.nodes }}</span>
        </div>
        <div class="hint">就绪 / 总数</div>
      </div>
      <div class="stat-card">
        <div class="label">容器组 (Pod)</div>
        <div class="value">{{ data.counts?.pods }}</div>
        <div class="hint">
          Running {{ data.podPhases?.Running || 0 }} · Pending {{ data.podPhases?.Pending || 0 }}
        </div>
      </div>
      <div class="stat-card">
        <div class="label">部署 (Deployment)</div>
        <div class="value">{{ data.counts?.deployments }}</div>
        <div class="hint">命名空间 {{ data.counts?.namespaces }}</div>
      </div>
      <div class="stat-card">
        <div class="label">服务 (Service)</div>
        <div class="value">{{ data.counts?.services }}</div>
        <div class="hint">对外暴露入口</div>
      </div>
      <div class="stat-card">
        <div class="label">异常对象</div>
        <div
          class="value"
          :class="(data.abnormalCount || 0) > 0 ? 'danger' : 'ok'"
        >
          {{ data.abnormalCount || 0 }}
        </div>
        <div class="hint">
          <span class="clickable" @click="$router.push('/diagnostics')">查看详情</span>
        </div>
      </div>
    </div>

    <!-- 异常 TOP 列表 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span>异常对象（Top {{ abnormal.length }}）</span>
          <el-button
            size="small"
            type="primary"
            text
            :disabled="!abnormal.length"
            @click="$router.push('/assistant')"
          >
            让 AI 分析这批异常
          </el-button>
        </div>
      </template>

      <el-table :data="abnormal" size="small" border empty-text="当前没有检测到异常">
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">
              {{ row.severity === "critical" ? "严重" : "警告" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="kind" label="类型" width="110" />
        <el-table-column prop="namespace" label="命名空间" width="130">
          <template #default="{ row }">{{ row.namespace || "-" }}</template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column label="异常原因" min-width="260">
          <template #default="{ row }">
            <el-tag
              v-for="i in row.issues"
              :key="i.type"
              size="small"
              :type="i.severity === 'critical' ? 'danger' : 'warning'"
              effect="plain"
              style="margin-right: 4px"
            >
              {{ i.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="diagnose(row)">AI 诊断</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, ref } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { getOverview } from "@/api";

const openDiagnose = inject("openDiagnose");

const loading = ref(false);
const data = ref({});

const abnormal = computed(() => {
  const pods = (data.value.abnormalPods || []).map((p) => ({ ...p, kind: "Pod" }));
  const nodes = (data.value.abnormalNodes || []).map((n) => ({ ...n, kind: "Node" }));
  const wls = (data.value.abnormalWorkloads || []).map((w) => ({ ...w }));
  return [...nodes, ...wls, ...pods].slice(0, 30);
});

async function load() {
  loading.value = true;
  try {
    data.value = await getOverview();
  } finally {
    loading.value = false;
  }
}

function diagnose(row) {
  const ns = row.namespace || "";
  const res =
    row.kind === "Pod" ? "pods"
    : row.kind === "Node" ? "nodes"
    : row.kind === "Deployment" ? "deployments"
    : row.kind === "StatefulSet" ? "statefulsets"
    : "daemonsets";
  openDiagnose({ res, name: row.name, namespace: ns, kind: row.kind });
}

onMounted(load);
</script>

<style scoped>
.sub { font-size: 15px; color: #98a2b3; font-weight: 500; }
.hint { color: #98a2b3; font-size: 12px; margin-top: 4px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
</style>