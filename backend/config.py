"""平台级配置。所有可调参数集中在此，便于部署时用环境变量覆盖。"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

# ---------------------------------------------------------------------------
# kubeconfig
# ---------------------------------------------------------------------------
# 优先顺序：KUBECONFIG 环境变量 > K8S_CONFIG_PATH > 默认 D:\config
DEFAULT_KUBECONFIG = r"D:\config"


def kubeconfig_candidates():
    """按优先级返回 kubeconfig 候选路径。"""
    paths = []
    env_path = os.getenv("KUBECONFIG")
    if env_path:
        # KUBECONFIG 可能是 ; 或 : 分隔的多路径（与 kubectl 行为一致）
        paths.extend([p for p in env_path.replace(";", os.pathsep).split(os.pathsep) if p])
    explicit = os.getenv("K8S_CONFIG_PATH")
    if explicit:
        paths.append(explicit)
    paths.append(DEFAULT_KUBECONFIG)
    # 运行时上传的 kubeconfig 兜底存放在项目目录
    paths.append(os.path.join(PROJECT_DIR, "uploaded-kubeconfig.yaml"))
    return paths


# ---------------------------------------------------------------------------
# 服务端口
# ---------------------------------------------------------------------------
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
AI_PORT = int(os.getenv("AI_PORT", "5001"))

# AI 服务地址（供后端/前端引用）
AI_BASE_URL = os.getenv("AI_BASE_URL", f"http://127.0.0.1:{AI_PORT}")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", f"http://127.0.0.1:{BACKEND_PORT}")

# ---------------------------------------------------------------------------
# 大模型（AI 助手使用）
# ---------------------------------------------------------------------------
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# 行为开关
# ---------------------------------------------------------------------------
# 集群不可达时是否允许退回到内置演示集群（保证平台可离线演示）
ALLOW_DEMO_FALLBACK = os.getenv("ALLOW_DEMO_FALLBACK", "1") == "1"

# 危险操作集合：这些动作必须带 confirm=true 才能真正执行
DANGEROUS_ACTIONS = {
    "delete",
    "delete_collection",
    "scale",
    "restart",
    "cordon",
    "drain",
    "apply",
    "exec",
}

# 审计日志文件
AUDIT_LOG = os.getenv("AUDIT_LOG", os.path.join(PROJECT_DIR, "audit.log"))
