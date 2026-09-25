# K8s 管理平台（仿 Kuboard）+ AI 运维助手

一个可运行的 Kubernetes 管理平台：仿 Kuboard 的多集群资源管理界面 + 独立跑在
**5001** 端口的 **K8s 专用 AI 助手**，能读集群、能操作集群、能分析异常并给出修复方案。

---

## 一、快速开始

```bash
# 1. 安装后端依赖
pip install -r requirements.txt

# 2. 安装前端依赖
cd frontend && npm install && cd ..

# 3. 配置环境变量（可选，用于 AI 助手）
copy .env.example .env    # 填入 DEEPSEEK_API_KEY

# 4. 一键启动（后端 8000 / AI 5001 / 前端 5173）
start.bat
```

访问 <http://localhost:5173>。停止服务用 `stop.bat`。

| 服务 | 端口 | 说明 |
|---|---|---|
| 平台后端 | 8000 | K8s REST 封装、诊断、审计 |
| **AI 助手** | **5001** | 大模型对话 + 工具调用 + 排错知识库 |
| 前端 | 5173 | 仿 Kuboard 管理界面（vite 代理到 8000/5001） |

---

## 二、集群接入

平台按以下优先级查找 kubeconfig：

1. 环境变量 `KUBECONFIG`
2. 环境变量 `K8S_CONFIG_PATH`
3. **`D:\config`**（你指定的路径）
4. 项目目录下的 `uploaded-kubeconfig.yaml`

若都读不到（当前环境就是这种情况），平台自动进入**演示模式**，用内置模拟集群
（含 CrashLoopBackOff / ImagePullBackOff / Pending / OOMKilled 等真实故障场景）
完整演示所有功能。

接入真实集群的两种方式：

- 把 kubeconfig 放到 `D:\config`，重启后端；
- 打开界面「集群接入」页，粘贴 kubeconfig 内容或选择文件导入。

> 注意：若 kubeconfig 里的 `server` 是 `127.0.0.1:6443` 或内网地址，需确认本机能访问该地址；
> 证书 SAN 不匹配时会报证书错误，可改用集群内网 IP 或把该 IP 加入证书 SAN。

---

## 三、目录结构

```text
k8s-platform/
├── backend/                     # 平台后端（8000）
│   ├── config.py                # 配置：kubeconfig 查找顺序、端口、危险操作清单
│   ├── registry.py              # 资源注册表（kind → api/plural/是否命名空间级）+ 公共异常
│   ├── diagnose.py              # 异常判定规则（Pod/Node/工作负载），前后端与 AI 共用
│   ├── kube.py                  # 真实集群客户端：kubeconfig 加载 + 通用 REST 通道
│   ├── demo.py                  # 演示集群：与 KubeClient 接口完全一致的内存实现
│   ├── audit.py                 # 审计日志（JSONL）
│   └── app.py                   # REST 路由层
│
├── ai/                          # AI 助手（5001）
│   ├── knowledge.py             # K8s 排错知识库（14 类异常的根因/排查/修复/易错点）
│   ├── tools.py                 # AI 可调用的 14 个工具（只读 9 + 写操作 5）
│   ├── agent.py                 # Agent 主循环：快照注入 + function calling + 降级策略
│   └── app.py                   # AI 服务路由
│
└── frontend/                    # 前端（5173）
    └── src/
        ├── utils/request.js     # axios 封装（统一解包 / 428 二次确认拦截）
        ├── api/                 # 后端与 AI 的接口定义
        ├── stores/cluster.js    # Pinia：集群状态、资源注册表、知识库
        ├── router/index.js      # 路由（资源页由配置表批量生成）
        ├── layout/AdminLayout.vue
        ├── components/          # Markdown 渲染 / YAML / 日志抽屉 / AI 诊断抽屉
        └── views/               # 总览 / 资源列表 / 异常巡检 / AI 助手 / 集群接入
```

---

## 四、关键设计（为什么这样写）

### 1. 一条通用 REST 通道，而不是几十个 typed client 方法

`backend/kube.py` 不逐个封装 `CoreV1Api.list_namespaced_pod(...)` 这类方法，而是用
`ApiClient.call_api` 按 `api/v1` 与 `apis/<group>/<version>` 拼路径。
好处：任何资源（**包括 CRD**）都能统一 list/get/patch/delete，
新增资源类型只需在 `registry.py` 加一行，前端菜单会自动出现。

### 2. 演示后端与真实后端同接口

`DemoBackend` 与 `KubeClient` 方法签名完全一致，`get_client()` 负责选择并返回。
上层路由、AI 工具链完全不感知差异 —— 所以集群连不上时功能不会「消失」，只是数据变成模拟的。

### 3. 异常判定只有一份实现

`diagnose.py` 是 Pod/Node/工作负载异常的唯一判定源，被三处复用：

- 后端 `/api/diagnostics`（界面红色告警）
- AI 工具 `scan_cluster` / `diagnose_object`
- 前端表格行染色

因此不会出现「界面标红但 AI 说正常」这类矛盾。

### 4. AI 的操作与人走同一条路径

AI 工具**直接调用 backend 的 service 层**（而非 HTTP），因此：

- 校验规则相同（`replicas > 200` 之类同样被拦）
- 审计记录写在同一份日志，字段带 `actor=ai` / `source=ai-agent`

### 5. 危险操作三道闸

| 层级 | 机制 |
|---|---|
| 工具定义 | 写操作工具单独归入 `WRITE_TOOLS`，AI 调用时**不会执行**，只返回待确认动作 |
| 接口 | 后端 `/api/execute`、`/api/resources/...` 校验 `confirm=true`，否则返回 **428** |
| 界面 | 前端收到后弹窗展示操作描述与风险等级，用户确认才下发 |

另外 `delete_resource` 被标为 `critical` 风险，前端会额外提示影响范围。

### 6. AI 的事实底座：每轮注入集群快照

`agent.py` 在每次对话前把「节点/Pod/命名空间数量、异常清单」拼进提示词。
这样即使模型不调用工具，也不会凭空编造资源名；调用工具只是用来下钻细节。

### 7. 无 API Key 也能用

未配置 `DEEPSEEK_API_KEY` 时自动降级为**规则引擎分析**：用 `diagnose` 的结果 +
`knowledge` 的根因/方案生成结论。模型调用失败（超时、限流）也会降级，并明确告知原因。

---

## 五、AI 助手能力

**14 个工具**（`GET /api/tools` 可查）：

| 类型 | 工具 |
|---|---|
| 只读（9） | `get_cluster_overview`、`scan_cluster`、`list_resources`、`get_resource`、`get_pod_logs`、`get_events`、`diagnose_object`、`get_node_detail`、`get_service_endpoints` |
| 写（5，需确认） | `scale_workload`、`restart_workload`、`delete_resource`、`cordon_node`、`exec_in_pod` |

**知识库覆盖的异常**（`backend` 与 `frontend` 共用）：
CrashLoopBackOff、ImagePullBackOff、Pending、OOMKilled、CreateContainerConfigError、
NotReady、HighRestarts、NodeNotReady、ReplicaUnavailable、NotAvailable、ProgressStalled、
PVC、ServiceNoEndpoints、DNS、Terminating。

每条包含：含义 / 常见根因 / 排查命令 / 修复方案 / 易错点。

**AI 接口**：

```bash
# 对话（会结合集群实时数据与工具调用）
curl -X POST http://localhost:5001/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"prod 的 api-server 为什么一直重启","namespace":"prod"}'

# 单对象一键诊断
curl -X POST http://localhost:5001/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"resource":"pods","name":"api-server-7d4b9c8f5-xyz12","namespace":"prod"}'

# 执行 AI 提议的操作（必须 confirm=true）
curl -X POST http://localhost:5001/api/execute \
  -H "Content-Type: application/json" \
  -d '{"tool":"scale_workload","args":{"resource":"deployments","name":"nginx","namespace":"default","replicas":3},"confirm":true}'

# 集群巡检（纯规则引擎，不消耗模型额度）
curl http://localhost:5001/api/diagnostics

# 审计日志
curl "http://localhost:8000/api/audit?limit=50"
```

---

## 六、平台后端接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 当前模式 |
| GET | `/api/cluster/state` | 连接状态 / context / kubeconfig 路径 |
| POST | `/api/cluster/kubeconfig` | 导入 kubeconfig 或重新扫描 |
| GET | `/api/cluster/overview` | 总览聚合 |
| GET | `/api/cluster/registry` | 资源注册表（驱动前端菜单） |
| GET | `/api/resources/<res>` | 列表（支持 namespace/labelSelector），带异常标记 |
| GET | `/api/resources/<res>/<name>` | 详情 |
| GET | `/api/resources/<res>/<name>/yaml` | 取 YAML |
| POST | `/api/resources/apply` | apply YAML（可多文档） |
| DELETE | `/api/resources/<res>/<name>` | 删除（需 confirm） |
| POST | `/api/resources/<res>/<name>/scale` | 扩缩容（需 confirm） |
| POST | `/api/resources/<res>/<name>/restart` | 滚动重启（需 confirm） |
| GET | `/api/pods/<ns>/<name>/logs` | 日志（支持 `previous=1` 看崩溃前日志） |
| POST | `/api/pods/<ns>/<name>/exec` | 容器内执行（需 confirm） |
| POST | `/api/nodes/<name>/cordon\|uncordon` | 节点调度（需 confirm） |
| GET | `/api/diagnostics` | 全集群异常巡检 |
| GET | `/api/audit` | 审计日志 |

---

## 七、安全注意事项

- `uploaded-kubeconfig.yaml` 与 `.env` 已在 `.gitignore` 中，**不要提交**（内含集群凭证）。
- Secret 只返回键名，不返回 base64 值，避免凭证通过界面泄露。
- 生产的审计日志建议改用集中式日志（`AUDIT_LOG` 指向可采集位置）。
- 平台默认 `ALLOW_DEMO_FALLBACK=1`。若你希望集群连不上时**直接报错**而不进演示模式，
  设为 `0`。
