# DRAGON-AI 工作流编排模块（service_workflow / workflow_engine）

> 参照 MaxKB 2.10.5 的工作流编排「设计」移植，按 DRAGON-AI 的「模型广场 + 模型网关」
> 体系重写实现。生产级、功能完整、异步驱动。

---

## 1. 概述

在 `service_workflow` 内新增了一套完整的工作流编排引擎，与既有的 MCP / 工具 / 沙箱 /
模型对话路由**共存**（不会破坏原有功能）。能力覆盖：

- 画布编排（VueFlow 风格图：`{nodes, edges}`）、草稿保存、发布、版本快照、回滚、复制、
  校验
- 20 种节点类型全集（START/END/LLM/AGENT/IF_ELSE/LOOP/ITERATION/PARALLEL/CODE/TEMPLATE/
  HTTP_REQUEST/TOOL/KNOWLEDGE_RETRIEVAL/PARAMETER_EXTRACTOR/QUESTION_CLASSIFIER/
  LIST_OPERATOR/VARIABLE_AGGREGATOR/VARIABLE_ASSIGNER/DOC_EXTRACTOR）
- 同步执行 / 异步执行（返回 executionId）/ SSE 实时事件流 + 结束回放
- 节点级「返回内容」开关（emitOutput）：每个节点可控制其输出 / 流式内容是否推送给客户端，数据持久化不受影响
- 调试：断点暂停、恢复、取消、变量实时修改、执行快照、快照恢复
- 工作流模板（内置 + 自定义）、导入/导出
- 工作流 API Key（调用鉴权、限流、过期）
- 文件上传（工作流内文件节点使用）

前端契约文件（**冻结，后端唯一标准**）：
`ui-ai/apps/web-antd/src/api/ai-workflow/index.ts`（与 `types.ts`）。

---

## 2. 目录结构

```
service/service_workflow/
├── __init__.py                     # FastAPI 应用装配（在既有 4 个路由基础上新增 6 个工作流路由）
├── workflow_engine/                # 执行引擎（与业务/ORM 解耦，仅依赖 common.*）
│   ├── engine.py                   #   asyncio 调度器 WorkflowRuntime（并行/汇聚/分支/子图/暂停恢复）
│   ├── graph.py                    #   图解析 / 拓扑索引 / 严格校验
│   ├── context.py                  #   三级上下文 + {{节点.变量}} 渲染
│   ├── comparators.py              #   条件比较器（CONTAINS / REGEX / IN ...）
│   ├── events.py                   #   事件总线（SSE 双消费者：实时 + 重放）
│   ├── model_client.py             #   模型广场兼容层（OpenAI 兼容直连 provider）
│   ├── node_definitions.py         #   node-definitions 接口数据源（画布组件面板）
│   ├── templates.py                #   内置模板种子
│   └── nodes/                      #   19 种节点执行器 + 注册表 NODE_REGISTRY
├── services/
│   ├── workflow_service.py         #   工作流 CRUD / 发布 / 版本 / 模板 / 下拉数据源 / ensure_initialized
│   ├── workflow_execution_service.py # 执行(同步/异步) / SSE 订阅 / 控制 / 调试 / 模型 Provider
│   ├── workflow_apikey_service.py  #   API Key 管理
│   └── workflow_file_service.py    #   文件上传/管理
├── routers/
│   ├── workflow_router.py          #   /workflows、/workflow-templates、/models、/knowledge-bases、/chat-agents
│   └── workflow_execution_router.py#   /workflow-executions、/workflow-api-keys、/workflow-files
├── schemas/workflow_schema.py     #   请求/响应 Pydantic Schema
└── models/workflow_entity.py      #   ORM 实体（tb_workflow* 7 张表）

独立部署的迁移脚本：sql/workflow_schema.sql（7 张表，IF NOT EXISTS，utf8mb4）
```

---

## 3. 挂载方式

`__init__.py` 在既有 `mcp_router / tool_router / sandbox_router / model_chat_router` 之外，
**手动挂载** 6 个工作流路由并统一追加 `ensure_initialized` 依赖：

```python
app = create_app(
    service_name=SERVICE_WORKFLOW, default_port=SERVICE_WORKFLOW_PORT,
    routers=[mcp_router, tool_router, sandbox_router, model_chat_router],
    enable_token_check=True, enable_operate_log=True,
)
for _r in (workflow_router, template_router, support_router,
           execution_router, api_key_router, file_router):
    app.include_router(_r, dependencies=[Depends(ensure_initialized)])
```

要点：

- 鉴权：`enable_token_check=True`，由网关注入 `X-User-Token`，`get_login_user` 解析；
  各写操作带 `has_permission(...)` 权限装饰器。
- SSE 订阅端点 `/workflow-executions/{id}/subscribe` **不挂**权限装饰器（EventSource 不能带
  自定义头，鉴权由网关完成）。
- `ensure_initialized` 是**幂等懒加载**依赖（不依赖 create_app 的 lifespan 时序）：
  首次请求时写入内置模板种子 + 注入默认模型 Provider；DB 未就绪时自愈（记日志、不阻断其他路由）。

---

## 4. 数据库迁移

在业务库执行 `sql/workflow_schema.sql`（与 `tb_model` 同库），建立 7 张表：

| 表 | 说明 |
|---|---|
| `tb_workflow` | 工作流主表（草稿区，编辑态写 `graph`） |
| `tb_workflow_version` | 发布版本快照（运行时只读 `graph_snapshot`） |
| `tb_workflow_execution` | 执行实例（一次运行 = 一条） |
| `tb_workflow_node_execution` | 节点执行明细（调试面板数据源） |
| `tb_workflow_template` | 模板（内置 + 自定义） |
| `tb_workflow_api_key` | 工作流 API Key |
| `tb_workflow_file` | 工作流文件元数据 |

> 设计约定：**编辑态写 `tb_workflow.graph`，发布时快照进 `tb_workflow_version`**；
> 运行时只读取版本快照，避免「执行中读到半编辑状态」。

---

## 5. 网关路由

前端 `BASE_URL = /api/workflow`。网关需将 `/api/workflow/**` 转发至本服务并**剥离前缀**
（既有 MCP / 对话 路由已依赖该别名，本次新增路由沿用，无需改网关配置）。

新增路由前缀一览（剥离 `/api/workflow` 后）：

| 前端调用 | 后端路由（本服务内） |
|---|---|
| `GET /workflows/page` | `workflow_router` |
| `GET /workflows/node-definitions` | `workflow_router` |
| `POST /workflows`、`PUT/DELETE /workflows/{id}` | `workflow_router` |
| `POST /workflows/{id}/publish`、`/rollback/{v}`、`/copy` | `workflow_router` |
| `GET /models/list`、`/knowledge-bases/list`、`/chat-agents/mine` | `support_router`（无前缀） |
| `GET/POST /workflow-templates/*` | `template_router` |
| `POST /workflow-executions/workflows/{id}/execute(-async)` | `execution_router` |
| `GET /workflow-executions/{id}`、`/page`、`/subscribe` | `execution_router` |
| `POST /workflow-executions/{id}/pause|resume|cancel` | `execution_router` |
| `PUT /workflow-executions/{id}/variables/{name}`、`/snapshot`、`/resume-from-snapshot` | `execution_router` |
| `POST /workflow-api-keys`、`GET /workflow-api-keys/workflows/{id}`、`/status`、`/delete` | `api_key_router` |
| `POST /workflow-files/upload(-batch)`、`GET/DELETE /workflow-files/{id}` | `file_router` |
| `POST /mcp-server/page`、`GET /mcp-server/{id}/tools` | **交由既有 `mcp_router` 处理**（同服务） |

响应统一为 `{code:200, message, data}`（`ApiResponse`），前端 `requestClient` 取 `data` 返回。

---

## 6. 模型调用（模型广场兼容层）

`workflow_engine/model_client.py` 的 `WorkflowModelClient` 是引擎唯一的模型入口：

- 配置来源：`GatewayCacheConfigProvider` —— 先读 Redis `model_rate_limit_config` 缓存，
  未命中回源 `tb_model`（`api_key` 不入 Redis，始终从 MySQL 读）。
- 调用格式：OpenAI 兼容
  - `is_direct=1`：`base_url` 已含完整路径（如 `.../v1/chat/completions`）
  - `is_direct=0`：取 `suffixes` 中 chat/embeddings/rerank 后缀拼接到 `base_url`
- 限流：复用 Redis 缓存里的 `rate_limit` 配置（与 service_gateway 一致）
- 可测性：`ModelProvider` 协议可替换（测试用 `MockModelProvider`）

> 已知限制（生产建议）：当前节点直连 provider。若要走「模型网关」统一鉴权/计费，
> 可将 `ModelConfig.chat_completions_url()` 指向 `tb_model.gateway_url` + 网关后缀，
> 并通过网关注入的内部 token 调用。此路径留作后续接入点。

---

## 7. 执行控制：暂停 / 恢复 / 取消 / SSE

架构（相较 MaxKB 的关键改进，见第 9 节）：

- `run()` 在整个执行生命周期内**持续存活**；命中断点后**阻塞**在 `_resume_signal`，
  不退出、不注销运行时，因此 SSE 与控制（resume/cancel）始终作用于同一运行时。
- `resume()` 为**轻量信号器**：仅切状态 + 放行信号，真正的重调度由 `run()` 暂停循环接管
  （避免「run() parked 而 resume() 另起炉灶」导致的任务泄漏 / bus 双重关闭）。
- 暂停时落库快照：`variables + nodeStates + pendingNodes`，支持跨进程 `resume_from_snapshot`
  重建运行时继续跑。
- **暂停态可取消**：`request_cancel()` 会同时放行信号，唤醒 parked 的 `run()`；若确实是暂停态
  则在唤醒后走取消分支（MaxKB 原设计在暂停时忽略取消请求）。
- **暂停自动回收 TTL**：暂停超过 `pause_ttl`（默认 1800s）由看门狗自动 `CANCEL`，避免 parked
  任务永久泄漏。
- **单节点超时**：每个节点执行包 `asyncio.wait_for`（默认 600s，节点 `data.timeout` 可覆盖），
  避免外部/工具/模型节点异常挂起拖垮整条工作流（MaxKB 无此保护）。

SSE：`subscribe_events(execution_id)` 支持运行中实时流与结束后 DB 回放两种模式。

---

## 8. 工作流 API Key

- 创建返回明文 `apiKey`（格式 `wf_xxxx`，仅一次），之后仅存哈希/掩码。
- 字段：`rateLimit`（0=不限）、`expireDays`、`status`（ACTIVE/DISABLED）。
- 用于对外暴露工作流为可调用的 HTTP 接口（鉴权 + 限流）。本模块提供 Key 的
  增删改查与状态管理；具体的「对外 HTTP 网关入口」由网关/调用层按 `apiKey` 校验后
  转发到 `execute-async` + `subscribe`。

---

## 9. 相比 MaxKB 的改进

| 维度 | MaxKB 2.10.5 | 本实现 |
|---|---|---|
| 并发模型 | 线程池 + 同步 | 全异步 `asyncio`，吞吐更高 |
| 暂停/恢复 | 断点重跑整链，运行时易泄漏 | `run()` 常驻 + 信号唤醒，单所有者，无泄漏 |
| 取消（暂停态） | 忽略取消请求 | 唤醒 parked run 并 CANCEL |
| 节点超时 | 无 | 单节点 `asyncio.wait_for` 超时保护 |
| 暂停回收 | 无（任务永久驻留） | TTL 看门狗自动 CANCEL |
| 快照恢复 | 进程内 | 持久化快照，支持跨进程 `resume_from_snapshot` |
| SSE | 实时 | 实时 + 结束后 DB 回放（断线可补） |
| 落库 | 聚合 | 聚合 `node_states` + 明细表双写 |
| 下拉数据源 | — | 模型/知识库/智能体，缺失表时优雅降级 `[]` |
| 调试 | 断点 | 断点 + 变量实时改 + 快照 + 快照恢复 |

---

## 10. 已知限制 / 后续接入点

1. **模型网关路径**：当前节点直连 provider（见第 6 节）。接入模型网关鉴权/计费时，
   改 `ModelConfig.chat_completions_url()` 指向 `gateway_url`。
2. **知识库检索节点**：`KNOWLEDGE_RETRIEVAL` 依赖 `service_rag` 的检索能力，
   需确认 `service_rag` 检索接口与权限（按 `kb_id` 可见性）。
3. **MCP / 工具节点**：`TOOL` / `HTTP_REQUEST` / `MCP` 节点依赖对应服务已就绪。
4. **API Key 对外网关入口**：本模块仅管理 Key 元信息，对外调用入口由网关层实现。

---

## 11. 测试

引擎与业务层在 `workflow-build/`（临时调试目录）下有一套 pytest 覆盖：图校验、比较器、
上下文渲染、模型客户端（MockTransport）、端到端（草稿→发布→执行：LLM+分支+AND 汇聚+
变量/模板+CODE 沙箱+LOOP+PARALLEL+异步 SSE 回放+断点暂停/恢复+版本回滚），
以及本模块新增的「节点超时」「暂停态取消」加固用例。合并进本目录的代码与临时目录同源。
