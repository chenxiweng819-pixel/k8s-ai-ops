<template>
  <el-drawer
    :model-value="modelValue"
    size="60%"
    :title="title"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="diag">
      <!-- 异常清单 -->
      <div v-if="issues.length" class="issues">
        <el-alert
          v-for="(i, idx) in issues"
          :key="idx"
          :type="i.severity === 'critical' ? 'error' : 'warning'"
          :closable="false"
          show-icon
          :title="`${i.type} · ${i.reason}`"
          :description="i.message || ''"
        />
      </div>
      <el-alert
        v-else-if="!loading"
        type="success"
        :closable="false"
        show-icon
        title="该对象当前未检测到异常"
      />

      <!-- 可执行的修复动作 -->
      <div v-if="actions.length" class="actions">
        <div class="section-title">建议的修复动作（需确认后执行）</div>
        <div class="action-row">
          <el-button
            v-for="a in actions"
            :key="a.key"
            size="small"
            :type="a.type"
            :loading="running === a.key"
            @click="a.run"
          >
            {{ a.label }}
          </el-button>
        </div>
      </div>

      <!-- AI 分析结论 -->
      <div v-if="answer" class="answer">
        <div class="section-title">
          <el-icon><MagicStick /></el-icon>
          AI 分析结论
          <el-tag v-if="mode" size="small" effect="plain" type="info" style="margin-left: 6px">
            {{ modeLabel }}
          </el-tag>
        </div>
        <Markdown :text="answer" />
      </div>

      <div v-if="mode === 'local'" class="tip">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="未配置模型 API Key，当前为规则引擎分析结果。配置 DEEPSEEK_API_KEY 后可获得结合日志与事件的深度分析。"
        />
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { MagicStick } from "@element-plus/icons-vue";
import { aiAnalyze, aiExecute } from "@/api/ai";
import { restartResource, scaleResource, deleteResource } from "@/api";
import Markdown from "./Markdown.vue";

const props = defineProps({
  modelValue: Boolean,
  target: { type: Object, default: null }, // {res, name, namespace, kind}
});
const emit = defineEmits(["update:modelValue", "changed"]);

const loading = ref(false);
const answer = ref("");
const issues = ref([]);
const mode = ref("");
const running = ref("");

const title = computed(() => {
  const t = props.target;
  if (!t) return "AI 诊断";
  return `AI 诊断 · ${t.namespace ? t.namespace + "/" : ""}${t.name}`;
});

const modeLabel = computed(() => ({
  llm: "大模型分析",
  local: "规则引擎",
  degraded: "降级分析",
}[mode.value] || mode.value));

// 按资源类型给出可执行的修复动作
const actions = computed(() => {
  const t = props.target;
  if (!t) return [];
  const list = [];

  if (["deployments", "statefulsets", "daemonsets"].includes(t.res)) {
    list.push({
      key: "restart",
      label: "滚动重启",
      type: "primary",
      run: () => doRestart(t),
    });
    list.push({
      key: "scale",
      label: "调整副本数",
      type: "default",
      run: () => doScale(t),
    });
  }

  if (t.res === "pods") {
    list.push({
      key: "delete",
      label: "删除并重建 Pod",
      type: "danger",
      run: () => doDeletePod(t),
    });
  }

  if (["nodes"].includes(t.res)) {
    list.push({
      key: "cordon",
      label: "禁止调度 (cordon)",
      type: "warning",
      run: () => doCordon(t, "cordon"),
    });
    list.push({
      key: "uncordon",
      label: "恢复调度 (uncordon)",
      type: "default",
      run: () => doCordon(t, "uncordon"),
    });
  }

  return list;
});

async function analyze() {
  const t = props.target;
  if (!t) return;
  loading.value = true;
  answer.value = "";
  issues.value = [];
  mode.value = "";
  try {
    const data = await aiAnalyze({ resource: t.res, name: t.name, namespace: t.namespace });
    answer.value = data.answer || data.summary || "";
    issues.value = data.issues || (data.diag && data.diag.issues) || [];
    mode.value = data.mode || (data.healthy ? "ok" : "");
  } catch (e) {
    answer.value = `诊断失败：${e.message}`;
  } finally {
    loading.value = false;
  }
}

function done(msg) {
  ElMessage.success(msg);
  emit("changed");
  analyze();
}

async function doRestart(t) {
  try {
    await ElMessageBox.confirm(
      `将滚动重启 ${t.namespace}/${t.name}，期间该工作负载的 Pod 会被逐个替换，可能出现短暂不可用。确认执行？`,
      "确认重启",
      { type: "warning", confirmButtonText: "确认重启", cancelButtonText: "取消" }
    );
  } catch (e) { return; }

  running.value = "restart";
  try {
    await restartResource(t.res, t.name, t.namespace);
    done("已触发滚动重启");
  } finally {
    running.value = "";
  }
}

async function doScale(t) {
  try {
    const { value } = await ElMessageBox.prompt("请输入新的副本数（0-200）", "调整副本数", {
      inputPattern: /^\d+$/,
      inputErrorMessage: "请输入非负整数",
      inputValue: "2",
      confirmButtonText: "确认",
      cancelButtonText: "取消",
    });
    running.value = "scale";
    await scaleResource(t.res, t.name, t.namespace, Number(value));
    done(`副本数已调整为 ${value}`);
  } catch (e) {
    // 用户取消时 ElMessageBox 会 reject，属于正常流程
  } finally {
    running.value = "";
  }
}

async function doDeletePod(t) {
  try {
    await ElMessageBox.confirm(
      `将删除 Pod ${t.namespace}/${t.name}。若它由 Deployment 等控制器管理，会自动重建；若为独立 Pod，则会被永久删除。确认？`,
      "确认删除",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "取消" }
    );
  } catch (e) { return; }

  running.value = "delete";
  try {
    await deleteResource("pods", t.name, t.namespace);
    done("Pod 已删除");
  } finally {
    running.value = "";
  }
}

async function doCordon(t, action) {
  try {
    await ElMessageBox.confirm(
      action === "cordon"
        ? `将节点 ${t.name} 标记为不可调度，现有 Pod 不受影响，但新 Pod 不会调度到该节点。确认？`
        : `将恢复节点 ${t.name} 的调度能力。确认？`,
      "确认操作",
      { type: "warning", confirmButtonText: "确认", cancelButtonText: "取消" }
    );
  } catch (e) { return; }

  running.value = action;
  try {
    await aiExecute("cordon_node", { name: t.name, action });
    done(`节点已${action === "cordon" ? "禁止调度" : "恢复调度"}`);
  } finally {
    running.value = "";
  }
}

watch(
  () => [props.modelValue, props.target && props.target.name],
  ([visible]) => {
    if (visible) analyze();
  }
);
</script>

<style scoped>
.diag { display: flex; flex-direction: column; gap: 14px; }
.issues { display: flex; flex-direction: column; gap: 8px; }
.section-title {
  font-weight: 650;
  font-size: 14px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.actions {
  border: 1px solid #e8ecf2;
  border-radius: 10px;
  padding: 12px 14px;
  background: #fff;
}
.action-row { display: flex; gap: 10px; flex-wrap: wrap; }
.answer { border-top: 1px dashed #e2e8f0; padding-top: 12px; }
</style>