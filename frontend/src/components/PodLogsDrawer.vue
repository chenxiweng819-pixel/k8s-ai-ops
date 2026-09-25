<template>
  <el-drawer
    :model-value="modelValue"
    :title="`容器日志 · ${namespace}/${pod}`"
    size="62%"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="log-head">
        <span class="mono">{{ namespace }}/{{ pod }}</span>
        <div class="log-actions">
          <el-select v-model="container" placeholder="容器" size="small" style="width: 150px" @change="load">
            <el-option v-for="c in containers" :key="c.name" :label="c.name" :value="c.name" />
          </el-select>
          <el-checkbox v-model="previous" size="small" @change="load">
            看上一次崩溃日志
          </el-checkbox>
          <el-select v-model="tail" size="small" style="width: 100px" @change="load">
            <el-option :value="100" label="100 行" />
            <el-option :value="300" label="300 行" />
            <el-option :value="1000" label="1000 行" />
          </el-select>
          <el-button size="small" @click="load">
            <el-icon><Refresh /></el-icon>&nbsp;刷新
          </el-button>
        </div>
      </div>
    </template>

    <div v-loading="loading" class="log-wrap">
      <el-alert
        v-if="previous"
        type="warning"
        :closable="false"
        show-icon
        title="正在查看上一次退出的容器实例日志，这是排查崩溃类问题最有效的手段"
        style="margin-bottom: 10px"
      />
      <pre class="log-body mono">{{ logs || "（无日志输出）" }}</pre>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, watch } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { getPodLogs, getPodContainers } from "@/api";

const props = defineProps({
  modelValue: Boolean,
  namespace: String,
  pod: String,
});
const emit = defineEmits(["update:modelValue"]);

const logs = ref("");
const loading = ref(false);
const containers = ref([]);
const container = ref("");
const previous = ref(false);
const tail = ref(300);

async function load() {
  if (!props.namespace || !props.pod) return;
  loading.value = true;
  try {
    const data = await getPodLogs(props.namespace, props.pod, {
      container: container.value || undefined,
      previous: previous.value ? 1 : undefined,
      tail: tail.value,
    });
    logs.value = data.logs || "";
  } catch (e) {
    logs.value = `获取日志失败：${e.message}`;
  } finally {
    loading.value = false;
  }
}

async function loadContainers() {
  try {
    const data = await getPodContainers(props.namespace, props.pod);
    containers.value = data || [];
    if (containers.value.length && !container.value) container.value = containers.value[0].name;
  } catch (e) {
    containers.value = [];
  }
}

watch(
  () => [props.modelValue, props.pod],
  async ([visible]) => {
    if (!visible) return;
    previous.value = false;
    await loadContainers();
    load();
  }
);
</script>

<style scoped>
.log-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.log-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.log-body {
  background: #0f1419;
  color: #d6e1ec;
  padding: 14px;
  border-radius: 8px;
  font-size: 12.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  min-height: 300px;
}
</style>