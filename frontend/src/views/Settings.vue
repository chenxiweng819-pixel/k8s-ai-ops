<template>
  <div>
    <div class="page-header">
      <span class="page-title">集群接入</span>
      <div class="page-actions">
        <el-button size="small" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;重新检测
        </el-button>
      </div>
    </div>

    <el-card shadow="never" style="margin-bottom: 14px">
      <template #header><span>当前连接状态</span></template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="运行模式">
          <el-tag :type="cluster.isDemo ? 'warning' : 'success'" size="small">
            {{ cluster.isDemo ? "演示模式（内置模拟数据）" : "已连接真实集群" }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Context">
          {{ cluster.state.context || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="kubeconfig 路径">
          <span class="mono">{{ cluster.state.kubeconfig || "-" }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="错误信息">
          <span :class="{ err: cluster.state.error }">{{ cluster.state.error || "无" }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="AI 助手">
          <el-tag :type="cluster.ai.online ? 'success' : 'danger'" size="small">
            {{ cluster.ai.online ? "在线（5001）" : "离线" }}
          </el-tag>
          <el-tag style="margin-left: 8px" size="small" effect="plain"
                  :type="cluster.ai.llmConfigured ? 'success' : 'warning'">
            {{ cluster.ai.llmConfigured ? `模型已配置：${cluster.ai.llmModel}` : "未配置模型 API Key" }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never">
      <template #header><span>接入真实集群</span></template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      >
        <template #title>
          平台按以下优先级查找 kubeconfig：环境变量 <b>KUBECONFIG</b> →
          <b>K8S_CONFIG_PATH</b> → <b>D:\config</b> → 本项目目录下的
          <b>uploaded-kubeconfig.yaml</b>。
          若 D:\config 不存在或连不上，可在此粘贴导入。
        </template>
      </el-alert>

      <el-input
        v-model="pasted"
        type="textarea"
        :rows="14"
        spellcheck="false"
        class="mono"
        placeholder="在此粘贴 kubeconfig 内容（以 apiVersion: v1 开头），或点击下方按钮选择文件"
      />

      <div class="actions">
        <el-button @click="triggerFile">
          <el-icon><Upload /></el-icon>&nbsp;选择文件
        </el-button>
        <input ref="fileRef" type="file" style="display: none" @change="onFile" />
        <el-button type="primary" :loading="saving" :disabled="!pasted.trim()" @click="importConfig">
          导入并连接
        </el-button>
        <el-button :disabled="saving" @click="rescan">仅重新扫描 D:\config</el-button>
      </div>

      <el-divider />

      <div class="tips">
        <div class="tips-title">获取 kubeconfig 的常用方式</div>
        <ul>
          <li>在 master 节点执行：<code class="mono">cat /etc/kubernetes/admin.conf</code>（或 <code class="mono">~/.kube/config</code>），复制全文粘贴到上方。</li>
          <li>如果 kubeconfig 里的 server 是 <code class="mono">127.0.0.1:6443</code> 或内网地址，请确认本机能访问到该地址（Windows 上通常是 VMware/VirtualBox 的 NAT 或桥接网段）。</li>
          <li>若提示证书错误，说明 server 地址与证书 SAN 不匹配，可改用集群内网 IP 或把该 IP 加入证书 SAN。</li>
          <li>导入后平台会把凭证保存到项目目录的 <code class="mono">uploaded-kubeconfig.yaml</code>，请勿把该文件提交到 Git。</li>
        </ul>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh, Upload } from "@element-plus/icons-vue";
import { useClusterStore } from "@/stores/cluster";
import { importKubeconfig } from "@/api";

const cluster = useClusterStore();
const pasted = ref("");
const saving = ref(false);
const fileRef = ref();

async function load() {
  await cluster.refreshState();
  await cluster.refreshAi();
}

function triggerFile() {
  fileRef.value?.click();
}

function onFile(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    pasted.value = String(reader.result || "");
    ElMessage.success(`已读取 ${file.name}，请点击「导入并连接」`);
  };
  reader.readAsText(file);
  e.target.value = "";
}

async function importConfig() {
  if (!pasted.value.trim()) return;
  saving.value = true;
  try {
    const info = await importKubeconfig(pasted.value.trim());
    ElMessage.success(`连接成功，context：${info.context}`);
    await cluster.bootstrap();
  } catch (e) {
    // request.js 已经弹了错误提示，这里只补一条排查建议
    ElMessageBox.alert(
      `连接失败：${e.message}\n\n请检查：\n1. server 地址本机是否可达（可用 telnet 测试 6443 端口）\n2. 证书 SAN 是否包含该地址\n3. 凭证是否过期`,
      "排查建议",
      { type: "warning", confirmButtonText: "知道了" }
    ).catch(() => {});
  } finally {
    saving.value = false;
  }
}

async function rescan() {
  saving.value = true;
  try {
    const info = await importKubeconfig(null);
    ElMessage.success(`连接成功，context：${info.context}`);
    await cluster.bootstrap();
  } catch (e) {
    ElMessage.warning(e.message);
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.err { color: #f56c6c; }
.actions { margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }
.tips { font-size: 13px; color: #5b6675; }
.tips-title { font-weight: 650; margin-bottom: 6px; }
.tips ul { margin: 0; padding-left: 20px; line-height: 2; }
code.mono { background: #eef2f7; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
.mono :deep(textarea) { font-family: "Cascadia Code", Consolas, monospace; font-size: 12.5px; }
</style>