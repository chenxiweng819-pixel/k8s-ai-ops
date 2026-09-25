import { aiHttp } from "@/utils/request";

// 注意：这些请求打到 5001 的 AI 服务，vite 会把 /ai 前缀剥掉
export const aiHealth = () => aiHttp.get("/health");
export const aiTools = () => aiHttp.get("/api/tools");
export const aiSnapshot = (namespace) => aiHttp.get("/api/snapshot", { params: { namespace } });
export const aiDiagnostics = (namespace, severity) =>
  aiHttp.get("/api/diagnostics", { params: { namespace, severity } });

// 排错知识库（与 AI 共用同一份，保证界面与 AI 结论一致）
export const aiKnowledge = () => aiHttp.get("/api/knowledge");

export const aiChat = (payload) => aiHttp.post("/api/chat", payload);

export const aiAnalyze = (payload) => aiHttp.post("/api/analyze", payload);

// 执行 AI 提议的写操作（必须 confirm=true）
export const aiExecute = (tool, args) =>
  aiHttp.post("/api/execute", { tool, args, confirm: true });

export const aiResetSession = (sessionId) => aiHttp.post("/api/session/reset", { sessionId });
