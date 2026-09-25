<template>
  <el-dialog
    :model-value="modelValue"
    :title="`YAML · ${res}/${name}`"
    width="820px"
    top="6vh"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-loading="loading">
      <el-alert
        v-if="readonlyHint"
        type="info"
        :closable="false"
        show-icon
        :title="readonlyHint"
        style="margin-bottom: 10px"
      />
      <el-input
        v-model="text"
        type="textarea"
        :rows="24"
        spellcheck="false"
        class="yaml-editor"
      />
    </div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
      <el-button @click="copy">复制</el-button>
      <el-button v-if="editable" type="primary" :loading="saving" @click="apply">保存并应用</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getResourceYaml, applyYaml } from "@/api";

const props = defineProps({
  modelValue: Boolean,
  res: String,
  name: String,
  namespace: String,
  // YAML 编辑属于高危动作，默认只读查看
  editable: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue", "applied"]);

const loading = ref(false);
const saving = ref(false);
const text = ref("");

const readonlyHint = props.editable
  ? "保存会直接替换集群中的该对象，请确认改动范围。"
  : "只读查看模式。修改 YAML 请到 Settings 或使用 kubectl apply。";

async function load() {
  loading.value = true;
  try {
    const data = await getResourceYaml(props.res, props.name, props.namespace);
    text.value = data.yaml || "";
  } catch (e) {
    text.value = "";
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.modelValue, props.res, props.name],
  ([visible]) => {
    if (visible && props.res && props.name) load();
  },
  { immediate: true }
);

async function copy() {
  try {
    await navigator.clipboard.writeText(text.value);
    ElMessage.success("已复制到剪贴板");
  } catch (e) {
    ElMessage.warning("复制失败，请手动选择文本复制");
  }
}

async function apply() {
  if (!props.editable) return;
  try {
    await ElMessageBox.confirm(
      "即将把上面的 YAML 应用到集群，这会覆盖同名对象的配置。确认继续？",
      "确认应用",
      { type: "warning", confirmButtonText: "确认应用", cancelButtonText: "取消" }
    );
  } catch (e) {
    return;
  }
  saving.value = true;
  try {
    const data = await applyYaml(text.value);
    ElMessage.success("已应用：" + JSON.stringify(data.results || []));
    emit("applied");
    emit("update:modelValue", false);
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.yaml-editor :deep(textarea) {
  font-family: "Cascadia Code", Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}
</style>