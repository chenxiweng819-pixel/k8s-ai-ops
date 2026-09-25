<template>
  <div class="assistant">
    <!-- 左：对话 -->
    <div class="chat-col">
      <div class="chat-head">
        <div>
          <div class="chat-title">
            <el-icon><MagicStick /></el-icon>
            AI 运维助手
          </div>
          <div class="chat-sub">
            <el-tag size="small" :type="cluster.ai.llmConfigured ? 'success' : 'warning'" effect="plain">
              {{ cluster.ai.llmConfigured ? `模型 ${cluster.ai.llmModel}` : "未配置模型（规则引擎模式）" }}
            </el-tag>
            <el-tag size="small" effect="plain" type="info">
              集群 {{ cluster.isDemo ? "演示" : "真实" }}
            </el-tag>
          </div>
        </div>
        <div class="chat-head-actions">
          <el-button size="small" text @click="reset">清空对话</el-button>
          <el-button size="small" text @click="loadSnapshot">刷新集群快照</el-button>
        </div>
      </div>

      <el-scrollbar ref="scrollRef" class="chat-scroll">
        <div class="msg-list">
          <!-- 空态：给出可点击的引导问题 -->
          <div v-if="!messages.length" class="empty">
            <div class="empty-title">我可以帮你做什么？</div>
            <div class="quick-list">
              <div v-for="q in quickPrompts" :key="q" class="quick" @click="send(q)">
                {{ q }}
              </div>
            </div>

            <div v-if="snapshot" class="snap">
              <div class="snap-title">我看到的集群现状</div>
              <div class="snap-body">
                节点 {{ snapshot.counts?.readyNodes }}/{{ snapshot.counts?.nodes }} 就绪 ·
                Pod {{ snapshot.counts?.pods }} 个 ·
                命名空间 {{ snapshot.counts?.namespaces }} 个 ·
                <b :class="{ danger: (snapshot.abnormalCount || 0) > 0 }">
                  异常对象 {{ snapshot.abnormalCount || 0 }} 个
                </b>
              </div>
            </div>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="avatar" :class="m.role">
              {{ m.role === "user" ? "我" : "AI" }}
            </div>
            <div class="bubble">
              <!-- 工具调用轨迹 -->
              <div v-if="m.steps && m.steps.length" class="steps">
                <div v-for="(s, si) in m.steps" :key="si" class="step">
                  <el-icon class="step-icon">
                    <component :is="stepIcon(s.type)" />
                  </el-icon>
                  <span>{{ s.text }}</span>
                </div>
              </div>

              <!-- 待确认的写操作 -->
              <div v-for="a in m.pendingActions || []" :key="a.id" class="pending">
                <div class="pending-head">
                  <el-tag :type="riskTag(a.risk)" size="small" effect="dark">
                    {{ riskLabel(a.risk) }}
                  </el-tag>
                  <span>AI 想执行这个操作</span>
                </div>
                <div class="pending-desc">{{ a.description }}</div>
                <div class="pending-actions">
                  <el-button
                    size="small"
                    type="primary"
                    :loading="running === a.id"
                    :disabled="a.executed"
                    @click="confirmAction(m, a)"
                  >
                    {{ a.executed ? "已执行" : "确认执行" }}
                  </el-button>
                  <el-button size="small" :disabled="a.executed" @click="a.dismissed = true">
                    忽略
                  </el-button>
                </div>
                <div v-if="a.result" class="pending-result mono">{{ a.result }}</div>
              </div>

              <Markdown v-if="m.content" :text="m.content" />
              <div v-if="m.loading" class="typing"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>
      </el-scrollbar>

      <div class="composer">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="描述你遇到的问题，例如：prod 的 api-server 一直在重启，帮我看看根因（Enter 发送 / Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send()"
        />
        <div class="composer-bar">
          <el-checkbox v-model="attachFocus" :disabled="!focus">
            附带当前对象上下文{{ focus ? `（${focus.namespace || ""}/${focus.name}）` : "" }}
          </el-checkbox>
          <el-button size="small" type="primary" :loading="sending" :disabled="!input.trim()" @click="send()">
            发送
          </el-button>
        </div>
      </div>
    </div>

    <!-- 右：AI 能力与工具 -->
    <div class="side-col">
      <el-card shadow="never" class="side-card">
        <template #header><span>可用工具（{{ tools.length }}）</span></template>
        <div class="tool-list">
          <div v-for="t in tools" :key="t.name" class="tool-item">
            <el-tag :type="t.write ? 'warning' : 'info'" size="small" effect="plain">
              {{ t.write ? "写" : "读" }}
            </el-tag>
            <div class="tool-info">
              <div class="tool-name mono">{{ t.name }}</div>
              <div class="tool-desc">{{ t.description.replace(/【[^】]*】/g, "").trim() }}</div>
            </div>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="side-card">
        <template #header><span>安全机制</span></template>
        <ul class="safe-list">
          <li>只读查询由 AI 直接执行</li>
          <li>扩缩容 / 重启 / 删除 / 容器内执行 <b>必须经过你确认</b> 才会生效</li>
          <li>所有操作（含 AI 发起的）写入统一审计日志</li>
          <li>AI 无法访问平台未暴露的资源</li>
        </ul>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { MagicStick, Tools, InfoFilled, Pointer } from "@element-plus/icons-vue";
import { aiChat, aiTools, aiSnapshot, aiExecute, aiResetSession } from "@/api/ai";
import { useClusterStore } from "@/stores/cluster";
import Markdown from "@/components/Markdown.vue";

const route = useRoute();
const cluster = useClusterStore();
const openDiagnose = inject("openDiagnose");

const messages = ref([]);
const input = ref("");
const sending = ref(false);
const running = ref("");
const tools = ref([]);
const snapshot = ref(null);
const scrollRef = ref();
const attachFocus = ref(true);

const SESSION_ID = `web-${Date.now()}`;

// 支持从资源页跳转过来时带上 focus（当前关注对象）
const focus = computed(() => {
  const { res, name, ns } = route.query;
  return res && name ? { res: String(res), name: String(name), namespace: ns ? String(ns) : "" } : null;
});

const quickPrompts = [
  "帮我巡检整个集群，有哪些异常？按严重程度排序",
  "prod 命名空间的 api-server 一直重启，帮我分析根因",
  "集群里有哪些 Pod 处于非 Running 状态？分别是什么原因",
  "有个服务访问 502，帮我排查是 Service 还是 Pod 的问题",
  "哪些节点存在压力或不可调度？影响哪些 Pod？",
  "帮我找出所有镜像拉取失败的 Pod，并给出修复步骤",
];

function riskLabel(r) {
  return { critical: "高危", high: "高风险", medium: "中风险", low: "低风险" }[r] || "需确认";
}
function riskTag(r) {
  return { critical: "danger", high: "danger", medium: "warning", low: "info" }[r] || "warning";
}

// 步骤图标按类型映射到已导入的组件（Element Plus 图标未全局注册，不能用字符串名渲染）
const STEP_ICONS = { action: Pointer, notice: InfoFilled, tool: Tools };
function stepIcon(type) {
  return STEP_ICONS[type] || Tools;
}

async function scrollToBottom() {
  await nextTick();
  const el = scrollRef.value?.wrapRef;
  if (el) el.scrollTop = el.scrollHeight;
}

async function loadTools() {
  try {
    const data = await aiTools();
    tools.value = data.tools || [];
  } catch (e) {
    tools.value = [];
  }
}

async function loadSnapshot() {
  try {
    snapshot.value = await aiSnapshot(cluster.namespace || undefined);
  } catch (e) {
    snapshot.value = null;
  }
}

async function send(preset) {
  const text = (preset || input.value).trim();
  if (!text || sending.value) return;

  input.value = "";
  messages.value.push({ role: "user", content: text });

  // 用 reactive 包裹：push 进 ref 数组的是响应式代理，
  // 后续对 reply 的赋值才能触发视图更新（直接改原始对象不会触发）
  const reply = reactive({ role: "assistant", content: "", loading: true, steps: [], pendingActions: [] });
  messages.value.push(reply);
  sending.value = true;
  scrollToBottom();

  const payload = { message: text, sessionId: SESSION_ID };
  if (cluster.namespace) payload.namespace = cluster.namespace;
  if (attachFocus.value && focus.value) {
    payload.focus = { kind: focus.value.res, name: focus.value.name, namespace: focus.value.namespace };
  }

  try {
    const data = await aiChat(payload);
    reply.content = data.answer || "（无回复）";
    reply.steps = data.steps || [];
    reply.pendingActions = (data.pendingActions || []).map((a) => ({ ...a, executed: false }));
    reply.mode = data.mode;
  } catch (e) {
    reply.content = `**请求失败**：${e.message}`;
  } finally {
    reply.loading = false;
    sending.value = false;
    scrollToBottom();
  }
}

// 执行 AI 提议的写操作 —— 用户确认后才真正下发
async function confirmAction(msg, action) {
  try {
    await ElMessageBox.confirm(
      `${action.description}\n\n风险等级：${riskLabel(action.risk)}\n\n该操作会真实作用于集群，确认执行？`,
      "确认执行 AI 提议的操作",
      { type: "warning", confirmButtonText: "确认执行", cancelButtonText: "取消" }
    );
  } catch (e) {
    return;
  }

  running.value = action.id;
  try {
    const data = await aiExecute(action.tool, action.args);
    action.executed = true;
    action.result = JSON.stringify(data.result, null, 2);
    ElMessage.success("操作已执行，已记录审计日志");
    loadSnapshot();
  } catch (e) {
    action.result = `执行失败：${e.message}`;
  } finally {
    running.value = "";
  }
}

async function reset() {
  messages.value = [];
  try {
    await aiResetSession(SESSION_ID);
  } catch (e) { /* 忽略 */ }
  loadSnapshot();
}

// 从异常巡检页跳转过来时自动提问
onMounted(async () => {
  await cluster.bootstrap?.();
  loadTools();
  loadSnapshot();
  if (route.query.q) send(String(route.query.q));
});

// 切换命名空间后快照需要重取
watch(() => cluster.namespace, loadSnapshot);
</script>

<style scoped>
.assistant {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 14px;
  height: calc(100dvh - 84px);
}
.chat-col {
  background: #fff;
  border: 1px solid #e8ecf2;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chat-head {
  padding: 12px 16px;
  border-bottom: 1px solid #eef1f6;
  display: flex;
  align-items: center;
  gap: 10px;
}
.chat-title { font-weight: 650; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.chat-sub { margin-top: 6px; display: flex; gap: 6px; }
.chat-head-actions { margin-left: auto; display: flex; gap: 4px; }

.chat-scroll { flex: 1; }
.msg-list { padding: 16px; display: flex; flex-direction: column; gap: 18px; }

.empty { padding: 10px 0; }
.empty-title { font-size: 15px; font-weight: 650; margin-bottom: 12px; }
.quick-list { display: flex; flex-direction: column; gap: 8px; }
.quick {
  border: 1px solid #e0e7f1;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.quick:hover { border-color: var(--el-color-primary); background: #f6f9ff; color: var(--el-color-primary); }
.snap { margin-top: 18px; background: #f7f9fc; border-radius: 8px; padding: 12px 14px; }
.snap-title { font-weight: 650; font-size: 13px; margin-bottom: 6px; }
.snap-body { font-size: 13px; color: #4b5563; line-height: 1.7; }
.danger { color: #f56c6c; }

.msg { display: flex; gap: 10px; }
.msg.user { flex-direction: row-reverse; }
.avatar {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  display: grid; place-items: center; font-size: 12px; font-weight: 700;
}
.avatar.user { background: linear-gradient(135deg, #3a7afe, #6b5cff); color: #fff; }
.avatar.assistant { background: #eef3ff; color: #3a7afe; }
.bubble { max-width: 88%; }
.msg.user .bubble {
  background: #f0f4ff;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  white-space: pre-wrap;
}

.steps {
  background: #f7f9fc;
  border-left: 3px solid #c8d6f5;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 10px;
}
.step { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: #5b667a; line-height: 1.9; }
.step-icon { color: #7c93c4; }

.pending {
  border: 1px solid #f5dab1;
  background: #fdf8f0;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 10px;
}
.pending-head { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 650; }
.pending-desc { margin: 8px 0; font-size: 13.5px; }
.pending-actions { display: flex; gap: 8px; }
.pending-result {
  margin-top: 10px; background: #0f1419; color: #d6e1ec;
  padding: 10px; border-radius: 6px; font-size: 12px; white-space: pre-wrap;
}

.typing { display: flex; gap: 5px; padding: 6px 0; }
.typing span {
  width: 7px; height: 7px; border-radius: 50%; background: #9aa7bd;
  animation: blink 1.2s infinite ease-in-out both;
}
.typing span:nth-child(2) { animation-delay: 0.18s; }
.typing span:nth-child(3) { animation-delay: 0.36s; }
@keyframes blink {
  0%, 80%, 100% { transform: scale(0.7); opacity: 0.45; }
  40% { transform: scale(1); opacity: 1; }
}

.composer { border-top: 1px solid #eef1f6; padding: 12px 14px; }
.composer-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }

.side-col { display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }
.side-card { flex-shrink: 0; }
.tool-list { display: flex; flex-direction: column; gap: 10px; max-height: 340px; overflow-y: auto; }
.tool-item { display: flex; gap: 8px; }
.tool-info { flex: 1; min-width: 0; }
.tool-name { font-size: 12px; font-weight: 650; color: #3a7afe; }
.tool-desc {
  font-size: 11.5px; color: #7b8798; line-height: 1.5; margin-top: 2px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.safe-list { margin: 0; padding-left: 18px; font-size: 12.5px; color: #5b6675; line-height: 2; }

/* 窄屏下把侧栏收起来，保证对话区可用 */
@media (max-width: 1100px) {
  .assistant { grid-template-columns: 1fr; height: auto; }
  .side-col { flex-direction: row; flex-wrap: wrap; }
  .side-card { flex: 1 1 300px; }
  .chat-col { height: calc(100dvh - 160px); }
}
</style>