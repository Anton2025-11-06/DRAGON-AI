# DRAGON-AI 工作流系统详细设计（CRUD + 执行引擎）

> 面向二次开发的全量设计说明。所有代码位置以 `E:/project/DRAGON-AI/service/service_workflow/` 为根。

## 0. 变更记录（2026-09-08：取消 celery，统一 arq 异步执行）

> ⚠️ 本次重构后，下文 4.1/4.4/5.1/5.2/5.7 中关于「同步 execute 入口、request_pause/request_cancel 信号控制、TTL 看门狗、进程内注册表恢复」的旧描述**已被新模型取代**，阅读时请以本变更记录 + 代码注释为准。

1. **任务队列**：废除 celery，全部任务走 `arq_tasks/`（`WorkerSettings`，Redis **db=1**）投递执行。
2. **执行入口统一**：`execute`（同步）已删除，**仅保留 `execute_async`**。API 层投递任务到 arq 队列后立即返回 `executionId`，前端通过 SSE `subscribe` 接口 + executionId 订阅该次执行的事件流。
3. **跨进程事件通道为 Redis Pub/Sub（替代 Stream 轮询桥）**：engine 每次 emit（节点事件 + node.delta token 流）经 EventBus 注入的 pub hook 实时 `PUBLISH` 到频道 `workflow:evt:{execution_id}`；API 进程的 SSE 订阅者 `SUBSCRIBE` 同一频道实时消费。Pub/Sub 无历史消息，晚订阅已发生的事件由 DB 状态兜底（已终态回放终态帧）。原 `cross_worker.py`（Stream 桥 + 后台轮询）与 `EventBus.bridge_cursor/history_since` 均已删除。
4. **取消/暂停/恢复完全由执行记录表（tb_workflow_execution.status）驱动**，不再经过 arq 任务控制：
   - 前端触发 → API 接口层直接把执行记录状态改为 `CANCELLED` / `PAUSED`；
   - workflow-engine 每个 node 运行前都会做一次状态检查（`status_check_hook`）：`RUNNING` 继续执行；`CANCELLED` 结束并持久化；`PAUSED` 结束并持久化快照（variables 字段）；
   - 恢复：前端触发 `resume`（可携带编辑后的变量）→ API 改状态为 `RUNNING` 并重新向 arq 队列投递 resume 任务 → worker 消费后通过持久化快照 `restore()` 恢复工作流状态，继续向后执行。
5. **监控**：`service_system` 模块新增 ARQ 队列指标（深度/已到点/延迟/执行中/健康）与 worker 进程健康（心跳/统计）两个接口，前端提供「ARQ任务监控」菜单页可视化展示。
6. Arq 0.28 的 worker 心跳为「同队列共享一个健康 key（后写覆盖）」，监控接口返回的 worker 指标是最近一次心跳 worker 的统计，不代表所有进程。
7. **事件通道纯 Redis Pub/Sub（无进程内状态）**：`EventBus` 删除本地历史缓冲/订阅队列/close，每次 publish 直接交给注入的 pub hook 实时 PUBLISH；`execution_registry`（进程内 `{execution_id: Runtime}` 注册表）整体删除，SSE 订阅/快照统一走 Redis 频道与 DB（晚订阅 DB 兜底）。
8. **执行状态统一常量**：engine 定义 `STATUS_*`（PENDING/RUNNING/PAUSED/COMPLETED/CANCELLED/FAILED），service/arq/订阅器一律引用常量，禁止裸字符串。
9. **归档功能删除**：后端 archive 接口/方法、前端归档按钮/API/状态色标、相关测试用例全部移除，工作流状态仅剩 DRAFT/PUBLISHED。
10. **修复新触发执行输入丢失 Bug**：`_build_runtime` 直接以执行记录行 `inputs/breakpoints` 构造运行时，`restore()` 仅在存在快照（暂停恢复）时调用，避免空快照把新执行输入清空。
11. **节点「返回内容」开关（emitOutput）**：每个节点可在属性面板设置返回内容开关（缺省开启）——开则把该节点数据事件（`node.completed` 的输出、`node.delta` token 流）实时推送给客户端；关则这两个数据事件不广播（`node.started`/`node.failed` 与 workflow 级终态事件不受影响）。数据持久化（节点明细落库、执行状态记录）一直开启，与开关无关。

## 1. 总体架构（四层，依赖单向向下）

```
┌─────────────────────────────────────────────────────────┐
│ Routers（HTTP 层）                                        │
│  workflow_router.py        定义 CRUD/发布/版本/模板       │
│  workflow_execution_router.py  执行/SSE/控制/ApiKey/文件  │
├─────────────────────────────────────────────────────────┤
│ Services（业务层）                                        │
│  workflow_service.py         定义域：草稿-快照模型        │
│  workflow_execution_service.py 执行域：Runtime 装配/落库  │
├─────────────────────────────────────────────────────────┤
│ workflow_engine/（引擎层，纯算法，不依赖 ORM/Redis）       │
│  engine.py    WorkflowRuntime 调度器（对标 MaxKB           │
│               WorkflowManage）                            │
│  graph.py     图解析/索引/静态校验（可独立单测）            │
│  context.py   三级作用域变量解析 + {{}} 模板渲染           │
│  events.py    WorkflowEvent + EventBus（纯事件发射，无本地缓存/注册表）│
│  nodes/       19 种节点执行器（注册表模式）                │
│  model_client.py  模型调用（tb_model + Redis 缓存 Provider）│
├─────────────────────────────────────────────────────────┤
│ Models（ORM 层）workflow_entity.py                        │
│  tb_workflow / tb_workflow_version / tb_workflow_template │
│  tb_workflow_execution / tb_workflow_node_execution       │
│  tb_workflow_apikey                                       │
└─────────────────────────────────────────────────────────┘
```

**关键设计约束**：
- 引擎层（engine/graph/context/events/nodes）**零 ORM/Redis 依赖**——持久化通过两个回调钩子（`node_persist_hook` / `state_persist_hook`）由 service 层注入。这就是 `node_test.py` 能脱离 DB 直驱引擎的原因。
- 节点与引擎解耦靠 `NODE_REGISTRY`（nodes/__init__.py:27）：`{node_type: ExecutorClass}`。

## 2. 数据模型与图契约

### 2.1 表结构（sql/workflow_schema.sql）

| 表 | 作用 | 关键字段 |
|---|---|---|
| `tb_workflow` | 主表，**graph 字段永远是草稿区** | graph(JSON), input_variables, output_variables, current_version, status(DRAFT/PUBLISHED) |
| `tb_workflow_version` | 发布快照（不可变） | workflow_id, version, graph_snapshot, input/output_variables, change_log |
| `tb_workflow_execution` | 执行记录 | id(uuid), workflow_id, workflow_version, status, inputs, outputs, node_states, **variables(快照)**, error_message, tokens, current_node_id |
| `tb_workflow_node_execution` | 节点级执行明细 | execution_id, node_id, node_type, status, node_order, input, output, error, duration_ms |
| `tb_workflow_template` | 模板（内置种子 + 用户自建） | graph, is_built_in |
| `tb_workflow_apikey` | 对外 API 调用密钥 | workflow_id, api_key |

### 2.2 图 JSON 契约（graph.py:1-13，对齐前端 types.ts）

```json
{
  "nodes": [{"id": "n1", "type": "LLM", "label": "大模型",
             "position": {"x": 100, "y": 200}, "data": { /* 配置 */ }}],
  "edges": [{"id": "e1", "source": "n1", "sourceHandle": "output",
             "target": "n2", "targetHandle": "input"}]
}
```

**端口契约**：
- 普通输出端口 `"output"`，输入端口 `"input"`
- 分支输出端口 `"branch:{branchId}"` —— 三处使用：IF_ELSE 的条件分支、QUESTION_CLASSIFIER 的分类、PARALLEL/LOOP 的分支/循环体
- 异常分支端口 `"branch:exception"` —— 节点失败且画了 exception 出边时走（engine.py:308-311）

### 2.3 节点配置在 `node.data`（即执行器里的 `self.config`）

典型约定：START 的 `fields`（输入字段定义）、END 的 `outputs`（输出变量映射）、LLM 的 `modelId/prompt`、分支节点的 `branches` 数组。

## 3. CRUD 设计：草稿-快照模型（workflow_service.py）

### 3.1 状态机

```
DRAFT ──publish──▶ PUBLISHED
```

### 3.2 核心语义（务必记住，二开易错点）

1. **草稿区**：`tb_workflow.graph` 始终是草稿。update/save 只改草稿，保存时做**宽松校验**（`_validate_graph_soft`，ERROR 只提示不阻断——允许画一半保存）。
2. **发布**（publish, workflow_service.py:134）：
   - 严格校验（`WorkflowGraph.validate()` 的 ERROR 必须清零，否则抛 `WorkflowGraphError`）
   - 快照整图写入 `tb_workflow_version`，`current_version += 1`，状态 → PUBLISHED
3. **执行取图规则**（workflow_execution_service.py:112-124）：
   - `trigger_type == "DEBUG"` → 跑**草稿**
   - `API/AGENT` → 跑 **current_version 快照**（草稿怎么改都不影响线上）
4. **回滚**（rollback, workflow_service.py:214）：目标版本快照**覆盖草稿 → 立即重新发布为新版本**。历史版本不可变，所以回滚后 version 是递增的（如 v1,v2 → 回滚 v1 → 变 v3），**不是版本号回退**。
5. **删除**：级联删 version + apikey（注意：**不删执行记录**——执行历史保留做审计）。
6. **懒加载初始化**（`ensure_initialized`, workflow_service.py:507）：内置模板种子 + 默认模型 Provider 注入，以 FastAPI 依赖挂在所有工作流路由上（`__init__.py:36-39`），DB 未就绪时自愈重试。

### 3.3 校验器分级（graph.py:validate）

| 级别 | 含义 | 典型项 |
|---|---|---|
| ERROR | 阻断发布 | 缺 START、多个 START、节点 ID 重复、边引用缺失、自环、同一源多条入边到同一目标、未知节点类型 |
| WARNING | 提示 | 缺 END（输出为空）、不可达节点、悬空节点 |
| SUGGESTION | 建议 | — |

每个节点执行器可加自己的静态校验：实现 `validate_node(node, graph)` 静态方法（base.py:52-55），发布校验时被自动调用（graph.py:248-255）。

## 4. 执行服务层（workflow_execution_service.py）

### 4.1 两个执行入口

```
execute()       同步：await runtime.run()，返回完整结果（DEBUG 面板用）
execute_async() 异步：asyncio.create_task(_run())，立即返回 executionId
```

### 4.2 `_create_runtime` 装配流程（二开看这里）

```
取图（DEBUG=草稿 / 正式=快照） → WorkflowGraph 解析 + 严格校验
→ 写 tb_workflow_execution(RUNNING, uuid4)
→ WorkflowRuntime(graph, inputs, breakpoints,
     model_provider=GatewayCacheConfigProvider,   # 模型配置：Redis 缓存+MySQL 回源
     http_client=共享 httpx.AsyncClient,           # 连接池复用
     node_persist_hook=_persist_node,              # 节点状态变化落库
     state_persist_hook=_persist_state)            # 执行状态/快照落库
```

### 4.3 持久化钩子（引擎回调，非引擎依赖）

- `_persist_node`：RUNNING 时 insert 节点明细行；COMPLETED/FAILED 时按 `(execution_id, node_id, node_order)` 更新——**node_order 是复合节点的重跑序号**，LOOP 每轮重跑同一节点会产生多行。
- `_persist_state`：节点边界/结束/暂停时更新执行主表；**暂停时**额外把 `{global, context, snapshot}` 写进 `variables` 字段（断点恢复数据源）。

### 4.4 SSE 订阅与控制（全部依赖进程内注册表）

- `subscribe_events`：注册表找 runtime → `bus.subscribe(replay=True)`（历史缓冲重放 + 实时队列）；找不到 → DB 回放降级（构造伪事件流）。
- `pause/cancel`：`runtime.request_pause()/request_cancel()`；`resume`：仅发信号。
- `resume_from_snapshot`（:405）：进程内有 runtime → restore+信号唤醒；没有（**进程重启后**）→ 从 DB 快照重建 Runtime + `restore()` + 重新 `run()`（run() 检测 `_pending_nodes` 非空则从暂停点续跑，不重跑已完成节点）。
- `update_variable`：直接写 `runtime.ctx.global_vars`（暂停态改变量用）。

## 5. 引擎调度算法（engine.py，核心中的核心）

### 5.1 WorkflowRuntime 生命周期

```python
runtime = WorkflowRuntime(graph, execution_id, inputs, ...)
runtime.restore(snapshot)   # 仅暂停恢复;新触发 variables 为空则跳过
await runtime.run()         # 由 arq worker 任务驱动(execute-async)
# 事件:节点事件(含 node.delta)经 EventBus pub hook 实时 PUBLISH
# 到 Redis 频道(Pub/Sub),无进程内注册表与本地事件缓存
```

### 5.2 run() 主循环（engine.py:141-209）

```
emit(workflow.started)
→ 从 START（或快照恢复的 _pending_nodes）调度
→ _wait_running() 等所有衍生任务
→ 暂停循环：while _pause_requested or PAUSED:
      _do_pause()（状态落库+发事件+挂 TTL 看门狗）
      await _resume_signal.wait()   ← run() 在整个生命周期 parked 在这里
      恢复后重排 _pending_nodes 继续调度
→ 全图完成：status=COMPLETED，_collect_outputs()（合并所有 END 节点输出）
→ 异常：FAILED（落库+发事件+上抛）；取消：CANCELLED
→ finally: bus.close()
```

**为什么暂停时 run() 不退出**：保证后台任务存活，SSE 与 resume/cancel 始终作用于同一运行时（避免"另起炉灶"导致任务泄漏/总线重复关闭）。这是设计上的关键取舍。

### 5.3 调度递归 `_schedule(node_id)`（:236，对标 MaxKB run_chain_manage）

```
节点已完成？→ 跳过（LOOP 回边场景）
命中断点？→ 清除断点 + _pause_requested=True
已请求暂停？→ 塞进 _pending_nodes 返回
_execute_node(node)     # 真正执行
  ├─ 超时保护：asyncio.wait_for(timeout=node.data.timeout 或 600s)
  ├─ 失败：emit(node.failed) → 有 branch:exception 出边？
  │        ├─ 有 → 走异常分支（输出 {exception_message}）
  │        └─ 无 → 整个执行 FAILED
  └─ 成功：ctx.set_node_output + 记录 _completed_with_branch[node]=branch_id
_route_next(node, result)  # 路由后继
```

### 5.4 分支路由 `_route_next`（:344，对标 get_next_node_list）

```
result.branch_id 非空 → 匹配 "branch:{id}" 端口出边；否则匹配 "output" 端口
每个目标过 _barrier_ready 闸门（AND 汇聚）
就绪目标按 y 坐标排序：
  1 个 → create_task(_schedule)
  多个 → asyncio.gather 并行（上限 MAX_PARALLEL_BRANCHES=50，防画布错配任务爆炸）
```

### 5.5 AND 汇聚闸门 `_barrier_ready`（:405，对标 dependent_node_been_executed）

目标节点每条入边判定：
- 源已完成且**活跃端口 == 该边 sourceHandle** → 就绪
- 源已完成但走的不是这条边 → 跳过（IF_ELSE 未选中分支的汇聚边）
- 源未执行：`_is_reachable(源)` 可达 → 等待（return False）；不可达 → 跳过

`_is_reachable`（:372）是**动态可达性分析**：基于"已执行的决策"（_completed_with_branch）判断未激活分支上的节点是否还可能到达，避免未执行分支把汇聚节点永久阻塞。环保守视为可达。

### 5.6 复合节点子图（LOOP/ITERATION/PARALLEL）

- `run_subgraph(entry, exit, scope_vars)`（:430）：执行 entry 起始子图。**每轮进入前 `_reset_subgraph_nodes` 重置子图完成状态**（否则"已完成跳过"守卫会让循环体只跑一次）。`scope_vars` 压栈（ITERATION 的 item/index）。
- `run_branches(node, branch_ids, wait_strategy)`（:490）：PARALLEL 并行分支，ALL/ANY/FIRST 等待策略，超时取消 pending。**分支内异常必须上抛**（:537-541，否则并行节点"假成功"）。
- **自死锁防御**（三处注释都在讲这个坑）：`_wait_running`/`run_subgraph`/`run_branches` 等待的都是"进入时刻快照之后**衍生**的任务"（`preexisting = set(self._running_tasks)`），因为复合节点自身以 task 形式在 `_running_tasks` 里，且 `asyncio.current_task()` 在 wait_for 内层拿到的不是外层调度 task——凭"非自身"过滤必漏祖先 → 死锁。**二开改等待逻辑时必须沿用快照排除法**。

### 5.7 控制

- `request_pause`：置位标志，调度器在下一个节点边界感知。
- `request_cancel`：置位 + 唤醒暂停态（暂停中取消立即生效）。
- 暂停 TTL 看门狗（`_auto_cancel_after`, 30 分钟）：防 parked 任务永久泄漏。

## 6. 变量系统（context.py）

### 6.1 三级作用域（高 → 低）

```
global   工作流级全局变量（VARIABLE_ASSIGNER 写入 / 调试 API 写入）
node     {node_id: {var: value}} 节点输出（含 START 输入）
local    scopes 栈（LOOP/ITERATION 的 item/index，栈顶优先）
```

### 6.2 引用语法与解析顺序

`{{nodeName.varName}}`，支持嵌套路径 `{{node.obj.field}}` / `{{node.arr.0}}`。

`resolve(ref)` 顺序（context.py:76-106）：
节点 id → 节点 **label**（画布上显示名也能引用）→ 已写 node_outputs → scopes 栈 → global（`global.xxx` 前缀或裸名）→ inputs → 带路径引用（head 是节点/global 则 `_dig` 路径下钻）。

### 6.3 渲染 `render(template, strict, keep_unresolved)`

- 递归处理 dict/list；非字符串原样
- `strict=True`：未解析抛 `VariableNotFound`（TEMPLATE 节点 strict 模式）
- `keep_unresolved=True`（默认）：保留 `{{ref}}` 原文，便于调试定位
- `keep_unresolved=False`：渲染为空串——END 节点多分支汇聚时用（control_nodes.py 的 END 执行器），未执行分支的引用不应残留最终输出
- dict/list 值 JSON 序列化为字符串

**已修过的坑**：END 纯引用匹配正则是 `([^{}]+?)` 而非 `(.+?)`——后者会把 `{{a}}{{b}}` 回溯误判为单一引用（control_nodes.py）。别改回去。

## 7. 事件与 SSE（events.py）

- `EventBus`（每执行一个）：**无本地缓冲/队列**——每次 publish 立即交给 service 注入的
  publish_hook 实时 PUBLISH 到 Redis 频道 `workflow:evt:{execution_id}`（含 node.delta
  token 级流式）；hook 缺失（引擎直驱测试）时事件直接丢弃。订阅者跨进程 SUBSCRIBE 同一频道
  实时消费（见 services/event_pubsub.py），晚订阅由 DB 状态兜底。
- 事件协议（前端 runtime-events.ts 按行解析）：`workflow.started/resumed/paused/completed/failed/cancelled` + `node.started/delta/completed/failed`。SSE 帧：`event: {type}\ndata: {json}\n\n`。
- 节点「返回内容」开关（画布 `data.emitOutput`，默认开）：开才广播该节点的数据事件——`node.delta`（nodes/base.py `emit_delta`）与 `node.completed`（engine `_execute_node`）；关则跳过。受开关影响的仅这两个事件；节点执行、`node.started`/`node.failed` 与数据持久化（落库）不受影响。

## 8. 新增一种节点（二开标准路径）

以加一个「消息通知」节点为例：

1. **执行器**：`workflow_engine/nodes/` 里新建（或塞进合适的文件）：
   ```python
   class NotifyNodeExecutor(BaseNodeExecutor):
       node_type = "NOTIFY"          # 与前端 types.ts NodeType 一致
       async def execute(self, ctx) -> NodeResult:
           channel = self.cfg("channel")
           content = ctx.render(self.cfg("content"))   # 模板渲染
           # ... 调用企微/钉钉 ...
           return NodeResult(output={"success": True})  # branch_id=None 走 output 端口
       @staticmethod
       def validate_node(node, graph) -> list:
           if not node.data.get("channel"):
               return [issue("NOTIFY_NO_CHANNEL", "ERROR", "未配置通知渠道", node)]
           return []
   ```
2. **注册**：`nodes/__init__.py` 的 `NODE_REGISTRY` 加一行 + `ALL_NODE_TYPES` 加类型名。
3. **前端**：types.ts NodeType、节点面板、属性面板表单、端口定义（如需分支端口 `branch:{id}`）。
4. **测试**：node_test.py 加用例（引擎直驱，无需起服务）。

节点能力速查：
- 流式输出：`await self.emit_delta(token)` → `node.delta` 事件
- 分支路由：`NodeResult(branch_id="xxx")` → 走 `branch:xxx` 端口
- 模型调用：`require_model_id()` + runtime.model_provider
- HTTP：复用 `runtime.http_client`（共享连接池）
- 子图（复合节点）：`runtime.run_subgraph(...)` / `run_branches(...)`

## 9. 已知坑与规约（血泪教训）

1. **loguru 禁止 f-string 内插异常**：`log.error(f"...{e}")` 在异常文本含 `{"code":403}` 时被 `.format()` 当占位符 → KeyError 逃逸路由层变 HTTP 400。必须 `log.error("...: {}", e)`。
2. **`_wait_running` 类等待必须用"进入时刻快照排除法"**，不能用 `current_task()` 过滤（wait_for 内层 task ≠ 外层调度 task）。
3. **END 多分支输出**：渲染必须 `keep_unresolved=False`。
4. **LOOP 每轮必须 `_reset_subgraph_nodes`**，否则循环体只跑一次。
5. **并行分支异常必须上抛**（gather 后检查 BaseException），否则假成功。
6. **变量解析失败返回 None 而非报错**（非 strict），排查"输出空"问题时先想引用没解析。
7. **执行记录不随工作流删除级联**（审计保留）；同名工作流允许存在（测试文本断言注意）。

## 10. 当前架构边界（分布式现状）

- ✅ API 层无状态可横向扩：CRUD/查询/创建执行走 Nacos + 网关（18000）负载均衡
- ✅ 执行事件已外置 Redis Pub/Sub：engine 每次 emit（节点事件 + node.delta）经 EventBus 注入的
  pub hook 实时 PUBLISH 到频道 `workflow:evt:{execution_id}`，任意进程的 SSE 订阅者 SUBSCRIBE
  实时消费；**无进程内注册表与本地事件缓存**（execution_registry 已删除）。取消/暂停/恢复走
  DB 状态控制（需求 5），不依赖本通道；进程挂掉执行记录停在 DB 等待人工处理（补偿扫描二期）
- ✅ 已留钩子：`resume_from_snapshot`（DB 快照重建）+ 节点边界 checkpoint（state_persist_hook）是断点续跑/故障迁移的地基

已完成的改造：执行队列化（arq，Redis db=1）✅、事件外置（Redis Pub/Sub，无后台桥）✅；
控制命令通道被 DB 状态控制替代（取消/暂停/恢复）。剩余：僵尸执行补偿扫描、节点级故障迁移。

## 11. 测试资产（E:/project/DRAGON-AI/test/workflow/）

| 脚本 | 说明 |
|---|---|
| `node_test.py` | 19 节点引擎直驱测试（46 用例，无需服务/DB） |
| `crud_test.py` | CRUD 全生命周期 API 测试（35 用例） |
| `browser_e2e.py` | 前端 CDP E2E（32 用例，幂等） |
| `cycle_test.py` | 创建→执行→发布→执行→删除循环压测 |
| `concurrent_test.py` | 并发执行隔离测试 |
| `cdp_driver.py` | Chrome CDP 直驱 WebSocket 工具 |
