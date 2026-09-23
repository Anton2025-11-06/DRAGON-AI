# 工作流「审批节点 + 指定 executionId 再提交 + 大模型记忆」设计冻结

> 本文记录审批节点与大模型记忆这两块的业务口径，代码与本文件冲突时以本文件为准。
> **执行 / 提交 / 事件的对外契约不在本文**：那份以 `docs/workflow-execution-contract.md` 为准（唯一入口
> `/submit`、统一出参 `SubmitResult`、待办只出自详情的 `pendingApprovals`）。本文引用其结论，不重复规定。

## 1. 被废弃的旧能力（先删后建）

| 旧能力 | 处置 | 原因 |
|---|---|---|
| 外部接口触发暂停 `POST /{id}/pause` | 删除 | 与新「审批节点硬暂停」语义冲突，暂停改由审批节点唯一驱动 |
| `POST /{id}/resume`（含携带 edit 变量） | 删除 | 由 `/submit` 带 `decisions` 的续跑承接 |
| `POST /{id}/resume-from-snapshot`、`PUT /{id}/variables/{name}` | 删除 | 前置条件都是「外部暂停态」；恢复改输入由 submit 的 `values` 承接 |
| 断点 `breakpoints` / 条件断点 `breakpointConditions` | 删除（前后端全链路） | 断点暂停依赖 resume，resume 已废；调试改由「审批节点 + 重新提交」覆盖 |
| arq 任务 `resume_workflow` | 删除 | 同上 |
| `GET /{id}/snapshot` | 保留 | 只读排障 |

`tb_workflow_execution.breakpoints` 列已 DROP（ORM 不再映射，迁移见 sql/approval_memory_columns.sql 第 6 段）。

## 2. 状态机（execution 级）

| 从 | 到 | 触发 |
|---|---|---|
| （无执行） | `RUNNING` | submit 不带 `executionId`：新建首跑 |
| `RUNNING` | `PAUSED` | 审批节点在节点边界抛 `AwaitingApproval`，本轮就此收尾 |
| `RUNNING` | `COMPLETED` / `FAILED` / `CANCELLED` | 跑完 / 节点失败 / 取消 |
| `PAUSED` | `RUNNING` | submit 带 `decisions`（本级那道审批）；或子执行进终态时由 `wake_parent_execution` 把父行推起（父等子，无人工动作） |
| `PAUSED` / 任一终态 | `RUNNING` | submit 带 `values`（追问一轮）或 `restart`（重开一轮） |

- **PAUSED 的唯一来源**：画布里的审批节点本轮未拿到审批结论。不再有「用户手动暂停」。
- **能不能再提交只看状态**：`RUNNING` / `PENDING` 一律拒（409「上一条还在执行中，可等待或取消」），`PAUSED` 与三个终态都可提交。判定唯一收口在 `execution/submit_resolver.py`。
- `TIMEOUT` 不作为 execution 级状态（它只是节点级并行砍分支的终态）。
- **不做双并发**：同一 execution_id 同时最多一个非终态实例，靠 `claim_for_run` / `wake_to_running` 的 CAS 更新保证（代次不符即已有人在推）。
- `PAUSED` 有两种长相，**只有详情分得清**：`pendingApprovals` 非空是「欠人答」，为空是「本轮已收尾、只欠一次唤醒」（父等子）。口径见 `execution_state.WatchState.awaiting_resume`。

## 3. 提交模式（内部列 `submit_mode`，调用方不传）

模式由 `submit_resolver` 按「给了哪几个键 + 这一行的现状」推出来（推导表见契约文档 §1.4），落成 `submit_mode` 列供 worker 重建运行时时取用；对外契约里没有这个字段。

| 值 | 含义 | 怎么被推出来 | 调度行为 |
|---|---|---|---|
| `RETRY` | 重新执行 | 带 `restart`，或终态行上的 `values` / 空请求 | 从 START **全部重跑**，不跳过任何节点 |
| `CONTINUE` | 答完审批接着跑 | 带 `decisions` 且本级有该节点未答的审批 | 从 START 遍历，命中「已 COMPLETED」的节点 **跳过（skip）**，到审批节点用本轮审批结论覆写并继续 |

**skip 判据（精确）**：`node_states[nid].status == COMPLETED` 才跳过；`FAILED/CANCELLED/TIMEOUT/RUNNING/AWAITING` 一律重跑。
审批节点特例：状态为 `AWAITING` 必然重跑；已是 `COMPLETED`（往轮已决策）按普通节点跳过，**不重复审批**。

**全流暂停时**（审批节点配置 `pauseScope=ALL`）：正在跑的其他分支被砍，节点停在 `RUNNING` 快照 → 下轮按上面的判据自动重跑，符合预期。

## 4. 事件契约

| 事件 | 触发点 | 载荷 |
|---|---|---|
| `node.paused` | 审批节点进入等待 | `nodeId`、`nodeType`、`duration` |
| `workflow.paused` | `run()` 收尾存在未决策审批节点 | `duration`；实时流另带 `variables`（回放不带） |
| `node.started` / `node.completed` | skip 节点也发 | `skip: true`；skip 的 completed **不带 output、不计耗时** |
| `node.cancelled` | 并行屏障「任一完成」短路（审批不同意**不再**砍任何节点） | 被短路分支**不带 input/output**，带 `reason` |

- **每一帧都带 `executionId` 与 `pauseGeneration`**（代次为 0 的帧不带后者）：只认本次订阅起点及之后的帧，迟到的旧轮帧据此丢弃。
- **帧上不承载任何审批内容**（待办清单、可编辑字段、暂停范围、审批人都不在帧里）：「此刻欠谁」只存 `pauseState` 一份，要看得 `GET /{executionId}` 取 `pendingApprovals`——暂停类帧只负责把页面推到「该看详情了」。
- 暂停类事件（`node.paused`/`workflow.paused`）在前端一律按「本轮结束」收敛，停 spinner、停轮询。
- 【工作流】节点 / LLM 节点内嵌的子工作流工具在等**子执行**里的审批时，父侧 `pendingApprovals` 多出的那一条就是那道审批的镜像（口径见 §11-12）。
- `workflow.resumed` 保留语义：答复审批唤醒成功后由 worker 首发一帧（见 §11-6）。

## 5. 审批节点（APPROVAL）

- 分类：`control`（前端面板显示「业务逻辑」）。
- 职责边界：**只收集人工结论，不参与流转控制**。单出口，同意与不同意都照常路由下游，工作流
  终态恒 `COMPLETED`；要按审批结果分流，下游自接 IF_ELSE 读 `review`（旧口径「不同意按
  `pauseScope` 取消下游闭包 / 整条流」已废弃，见 §11-11）。
- 输出：**透传给下游的上游输入数据**（口径见下）+ 恒有的结论三键 `review`（bool，是否同意）
  + `reviewOpinion` + `reviewBy`。
- 配置项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `approvers` | `array<number\|string>` | 允许审批的标识集合；留空=持有 api-key 且知道 executionId 者皆可 |
| `pauseScope` | `ALL` \| `DOWNSTREAM` | 暂停范围：只决定**等人工结论时停多大范围**（整条流（砍在跑分支）/ 仅本节点及下游），与结论是同意还是不同意无关 |
| `passThroughInputs` | `array<{nodeId, varName, path?, name?}>` | 表单显示名「选择输出参数」。要透传给下游的输入参数，可下钻到子字段；**留空=全部上游透传**，选了=只放行选中项。`path`=变量之下的子路径（`a.b` / `list[0].x`），`name`=透传给下游的键名（缺省取子路径末段，末段是纯数字时用 `<变量名>_<下标>`） |
| `timeoutHours` | number(0=不限) | 预留字段，一期不做自动裁决 |

- **透传口径**（后端 `ApprovalNodeExecutor._resolve_pass_through`，前端镜像在
  `components/variable-selector/upstream-variables.ts`；两边的合并顺序必须一致，否则
  「下拉里选得到、运行期取不到」）：
  - 未配 `passThroughInputs`：由近到远回溯**全部**上游（`upstream_layers`：隔着普通节点继续往上），
    同名键以**更近**的一份为准，最后铺 START 入参兜底；**上游审批仍是屏障**，隔着它的原始数据只能
    从那道审批自己放行的那份里拿（否则等于绕开审批人的编辑与结论）。
  - 配了 `passThroughInputs`：只按选中的 `(节点, 变量, 子路径)` 对取值，输出键名用配置里的 `name`
    （缺省走末段规则），按选择顺序**先选先胜**；源节点没跑出这个键就不占位（下游引用得到 `None`，
    而不是陈旧值）。选了某个 key 就**不会**把整个对象一并放出去。
  - 子字段的 key 从哪来（混合）：有结构声明的输出（目前全项目只有 LLM 的 `structuredOutput.jsonSchema`）
    在前端表单里自动展开成可勾选的 key 树（最多下钻 3 层）；没声明的（HTTP body、代码/工具/MCP 返回值、
    metadata、检索/迭代 results）运行后才知道有哪些 key，表单用「按子路径添加」手填 `path`。
  - 审批是下游变量下拉的**引用屏障**（`upstreamNodeIds` 命中 APPROVAL 即计入、不再回溯它的入边上游），
    所以下游拿上游数据只有一条路：引用审批节点的透传键。
- **审批人识别（开始节点「审批入参」）**：画布存在 APPROVAL 节点时，START 节点才允许添加一个 `type=APPROVER` 的输入字段（值为数组，元素 number|string）。提交时 `inputs[该字段]` 与审批节点 `approvers` 求交集；无交集 → 拒绝提交并提示无权限；`approvers` 为空则跳过校验。`reviewBy` 落库取该入参首个值，缺省时回落 login_user/token。
  画布上**任一**审批节点配了审批人就参与这条联动与身份校验（`graph.validate` / `verify_approver` / 前端诊断三处同一口径）。
  嵌套调用时这份身份怎么从父流带下来、审批面板里改了算不算数，见 §11-13。
- 编辑回写：可编辑清单（详情的 `pendingApprovals[].editableFields`，引擎内部形状仍是挂起上下文的
  `editableInputs`）与透传集**同源到 key 级**
  ——两者都出自同一个 `_resolve_pass_through`，放行什么才能改什么，但只剔除一类本轮不该改的值：
  **上游审批的结论键**（`review/reviewOpinion/reviewBy`，那是上一道审批的审计结果，可以透传给下游但
  不能改写，否则等于替别人补签结论）。**开始入参照常放行**（曾剔除，放开的原因见 §11-12）：审批人要核对、
  甚至当场改掉的往往就是上游传进来的那份值；回写只落在本轮 `node_states[开始节点].output`，重开一轮
  （`restart`）是一次全新执行、会被外部新入参覆写，不影响存量。
  服务端按字段名存着回写坐标 `(源节点, 变量名, 子路径)`（`pause_state.EditableField`），**对外只给
  `name` + 展示用的 `label`/`sourceNodeLabel` + 当前 `value`**；调用方按 `fieldValues: {name: 新值}`
  回传，服务端凭 `approvalToken` + 字段名查回坐标再拼成引擎要的四元组。带子路径的项只替换该子字段，
  同一变量里没被选中的其它键原样保留，子路径取不到值则整条编辑丢弃并告警。
  回写同时改 `node_states[源].output`、`ctx.node_outputs[源]`，并在审批节点 state 记 `reviewDiff`（含 `path`）。
  快照里是回写后的值（`execute()` 先 `apply_output_edits` 再解析透传）。**编辑只在同意时回写**：
  不同意只落结论，表单里的改动不落库。
- 审批节点一律禁止放入 LOOP / ITERATION / PARALLEL 子图（graph.validate ERROR）：子图每轮都会
  重新执行它，恢复语义（skip/覆写）在循环体里不成立。
- `passThroughInputs` 的源节点已被删除 → 逐节点校验 `APPROVAL_PASSTHROUGH_SOURCE` ERROR（运行期只会默默少一个透传键，发布前拦住）。
- 阻塞只发生在**节点边界**：审批节点不落 `execute()` 内部等待，`run()` 直接以 PAUSED 返回，不占 worker 任务槽、不受节点超时约束。

## 6. 可恢复状态从哪来

1. **权威源 = `node_states`**（每节点 `input/output/branch/llmMessages/review*`），不再依赖 `variables` 快照才能恢复。
2. `ctx` 重建：`inputs` ← 行 `inputs`；`node_outputs` ← `node_states[nid].output`；`executed`/`order` ← 按 order 排序；`global_vars` ← 按 order 重放 `VARIABLE_ASSIGNER` 的 output（LOOP 需把循环计数变量一并写进 output 才能重放）。
3. `_completed_with_branch` ← `node_states[nid].branch`（**新增字段**，`to_dict`/落库/详情回填同步）。
4. `variables` 列**只剩两段**：`roundRequest`（本轮要消费什么）与 `pauseState`（此刻在等谁），读写全经
   `ExecutionStateStore.encode_variables` / `decode_variables`，顶层不许再出现别的键；挂起代次不住这里，
   它是执行行的 `pause_generation` 列。两种模式都靠 node_states 重建，这列不再存执行快照。
5. 图一致性：执行时记 `graph_hash`（节点 id 集合 + 边集合的 sha1 前 16 位），再提交时不一致 → 拒绝并提示「画布已变更，请重新执行工作流」。DEBUG 预览运行同样受此保护（草稿会变）。
6. 落库全部改为 `await` 串行，不再 `asyncio.create_task` 火忘（避免上一轮迟到写覆盖新一轮数据）。

## 7. 统计与明细口径

| 项 | RETRY（重开 / 追问一轮） | CONTINUE（答完审批接着跑） |
|---|---|---|
| `tb_workflow_node_execution` 该 execution 的行 | 提交时**物理删除**，本轮重建 | 提交时物理删除，skip 节点也补写一行（`status=COMPLETED` + 快照 input/output + `skip:true`） |
| token / `llm_call_count` | 重新计算，不累加 | 跨轮累计 |
| `started_at` | 重置为本轮 | 保留最早值，`completed_at` 按轮更新 |
| `duration_ms` | 重置 | 跨轮累计 |
| 审批节点 `review/reviewBy/reviewDiff` | **清除**上轮残留 | 由本轮结论覆盖 |
| 副作用重放（HTTP/工具/生成） | 不拦截，由用户在下游做幂等 | — |
| `inputs` | 允许随提交更换，写回 `row.inputs` 并影响重建后的 `ctx.inputs` | 允许更换（新对话轮次的用户输入） |

## 8. 大模型记忆

- 权威存储：`node_states[nid].llmMessages` = `[{role, content, round, ts}]`（不新开列，随节点状态 JSON 落库）。
- **所有能力类型统一开记忆**，注入形态分三类：
  - `MT_TEXT_TO_TEXT`：历史作为 messages 段插在 system 之后、本轮 user 之前（动态渲染，**不回写节点 input**）。
  - 有文本输入槽的类型（图/视频理解、OCR、文生图/视频/音频、音频转文字）：历史压成「对话记录前缀」拼进 prompt。
  - 向量/多模态向量/图像向量/重排：**表单不展示、validate ERROR**（加历史会污染表征结果）。
- 配置项：`memoryEnabled`、`memoryLimit`(1..100，默认 10)、`memoryScope`(`SELF|NODES|WORKFLOW`)、`memoryNodes`(NODES 时指定)、`memoryStrategy`(`DROP_OLDEST|DROP_MIDDLE|DROP_NEWEST|COMPRESS`)、`memoryCompressModelId`。
- 条数语义 = **消息条数**（一轮 = user+assistant 两条）。单条 content 超 4000 字符截断。
- 隔离与聚合：按 `node_id` 隔离存储；`SELF` 只取本节点、`NODES` 取指定节点集合、`WORKFLOW` 取全图所有大模型节点，按 `round→order→写入序` 排序后统一截断。
- 压缩策略：把超限历史交给所选文生文模型摘要成一条 system 级摘要；**模型不可用/无权/调用失败 → 降级 `DROP_OLDEST` 并在节点 output 记 `memoryWarning`**；压缩消耗计入本执行 token。
- LOOP/ITERATION 体内的 LLM 节点：每轮**覆盖**本轮记忆（不追加），避免指数膨胀。
- `emitOutput=false` 只影响广播，记忆持久化照做。
- 多轮对话：`execution_id` 即会话 id —— 对话页/预览页在同一个 execution_id 上再提交一次带 `values` 的
  submit（内部判为 RETRY），记忆因此天然跨轮。

## 9. 接口

执行域对外只有一个动作：`POST /api/workflow/workflow-executions/submit`（不带 `executionId` 即新建），
预览页换个传输走 `WS` 同一路径、事件从这条连接回推；取消仍是 `POST /{execution_id}/cancel`。入参
只 5 个键、出参只有 `SubmitResult`、校验矩阵与幂等口径全在 `docs/workflow-execution-contract.md`
§1.2~§1.5，本文不重复。两条与审批/记忆相关的实现事实：

- 投递：`job_id = execution_id`，三种提交意图共用同一个 arq 任务（`execute_workflow` → `run_in_worker`），
  非终态唯一性保证不与在跑任务同名；被 arq 拒绝时按 `retry_times` 每次 sleep 1s 重试，仍失败 →
  `rollback_claim` 把状态退回提交前值并给「暂时无法提交，任务尚未彻底结束，请稍后再试」。
- 审批凭据过期即失效：`approvalToken` 答过、被同一节点重挂换号、或被取消/重开作废后再提交，
  一律回 200 `duplicated:true` + 当前 `pendingApprovals`，后台不做重复动作。

## 10. 已知边界（一期明确不做）

- 待审批列表/待办中心/审批通知（工作流对外输出，由外部系统自持）。
- 审批超时自动裁决（只留 `timeoutHours` 字段占位）。
- 多审批人会签/或签（`approvers` 仅做「命中其一即可」）。
- 复合节点子图内的暂停与恢复。
- 跨 execution 的全局会话记忆（记忆随 execution 走）。

## 11. 实现偏离与补记（开发按本节口径，与前面章节冲突时以本节为准）

1. **记忆禁用类型**：§8 原把「音频转文字 / 文生音频」归为可拼前缀类，实现时发现两者没有可注入
   的对话文本槽（ASR 输入只有音频；TTS 的 `text` 是「要念出来的内容」，拼历史会把整段对话念出
   来），已移到禁用清单。最终可开记忆 = 文生文 + 图/视频理解 + OCR + 文生图 + 文生视频 + 图生视
   频，前后端共用同一份清单（`memory.MEMORY_SUPPORT_CATEGORIES` ↔ `const.ts MODEL_TYPES_MEMORY`）。
2. **前缀只在节点已有 prompt 时注入**：OCR/图生视频的提示词是可选项，原本为空时硬塞一段对话记录
   会让厂商拿到一份没预期过的输入（图生视频会把它当画面描述）。
3. **记忆仅限 LLM 节点**：其他节点类型表单不出现记忆区，静态校验也只扫 `type == "LLM"`。
4. **断点列与求值入口**：`tb_workflow_execution.breakpoints` 列已 DROP（ORM 已不再映射，存量库由
   迁移脚本删列）；`comparators.py` 里的自由文本条件求值（`{{ref}}` 替换 + JS 运算符归一 + `eval`）唯一调用方
   是断点条件，随之整体删除 —— 留着一个 eval 入口只会被新功能误用。
5. **RETRY 额外清 `branch`**（§7 未列）：全量重跑不沿用上轮出边端口，且详情页会拿它渲染「分支 x」
   标签，留着就是未跑节点的假路由。
6. **`workflow.resumed` 事件**（§4 未列）：答复审批后续跑的首帧（与 `workflow.started` 二选一，两条
   都发会让订阅方把「恢复」当成新一轮执行重置），前端据此复位气泡里的待审批标记。
7. **状态字面量约定**：引擎侧节点态 `AWAITING` / execution 级 `PAUSED`；前端 trace 与画布统一用
   `awaiting`（曾出现 `waiting`/`awaiting` 混用，已全量改为 `awaiting`；`highlightNode` 入参仍用
   `paused`，由 `DebugPanel.toCanvasStatus` 做一层映射）。
8. **审批入口三处**：预览运行面板（PreviewRunner，往它持有的那条 WS 再发一帧 `decisions`）、对话气泡
   （WorkflowChatModal，走 HTTP submit + 重新订阅同一 executionId）、编辑页工具栏（仅引导到预览面板，
   不重复做一份表单）。三处面板的待办都来自详情的 `pendingApprovals`，不是事件帧。
9. **透传改为回溯全部上游 + 可选参数**（补 §5）：原口径只平铺直接入边来源（+ 开始入参），
   于是 开始→节点1→节点2→审批→节点3 里节点3 拿不到节点1；现在默认回溯全部上游，并新增
   `passThroughInputs` 让审批人自己收窄放行范围。前端推导从 `VariableSelector.vue` 抽到共享模块
   `variable-selector/upstream-variables.ts`（变量下拉与审批表单的透传候选同一个口径），镜像回归
   与后端回归共用一份拓扑：`test/_check_approval_passthrough.py`。
10. **选择输出参数下钻到 key 级**（补 §5）：透传项从 `{nodeId,varName}` 扩成
    `{nodeId,varName,path,name}`（存储键名不变，旧图不需迁移：缺 `path` 就是整值），审批表单
    改名「选择输出参数」并把有结构声明的输出展开成可勾选的 key 树（没声明的走手填子路径）；
    可编辑清单跟着同源到 key 级，并剔除上游审批的结论键。
    回归：后端 `test/_check_approval_passthrough.py`，前端镜像 `test/_check_upstream_variables.mts`
    （`node --experimental-strip-types` 直接跑 `upstream-variables.ts`）。
    注意：本次改了引擎侧挂起上下文的 `editableInputs` 项（新增 `path`）与节点执行器，**必须重启 arq 的 WORKFLOW worker**
    才生效：跑着旧代码的 worker 会把 `path` 当未声明字段丢掉，key 级编辑会退化成整值覆盖。
11. **回撤审批的流转控制**（修正 §5 与本节第 10 条曾加过的两个设计）：审批节点职责收窄回
    「只收集结论」，输出 `runNext`、`pauseScope=NONE`（不暂停）、`rejectReply`（拒绝回复
    文案）三者全部删除；不同意不再取消任何节点，下游照常跑完，分流由下游条件节点读 `review`
    自行决定（`runNext` 本身也不是 if/else，只是把控制信号换了个名字）。引擎侧
    `WorkflowRuntime.downstream_closure()` / `cancel_downstream()` / `reject_replies`（含快照键
    `rejectReplies`）随之删除；旧快照里的该键被直接忽略，不需迁移。三套校验回到不分
    `pauseScope` 的同口径：`validate_node` 只收 ALL/DOWNSTREAM、子图禁令无条件、配了审批人就要
    START 审批入参（后端 `graph.validate` 与前端 `validateWorkflowGraph` 同步）。回归同步改写：
    后端 `case_reject_keeps_downstream` / `case_edits_only_on_approve` / `case_validate_scope_and_name`，
    前端镜像全量透传段。**存量图里若还写着 `pauseScope=NONE` 或 `rejectReply`，发布后会被校验
    报 `APPROVAL_SCOPE` ERROR，需手改成 ALL/DOWNSTREAM**（旧配置不会自动迁移）。同样需重启
    arq 的 WORKFLOW worker 才生效。
12. **【工作流】节点与 LLM 内嵌子工作流工具卡在子流程审批上时，父侧登记的是一份镜像待办**：
    子执行落 PAUSED，父节点在节点边界抛 `AwaitingApproval`（`kind=CHILD_WORKFLOW`），两条来路
    共用 `subworkflow_nodes.build_awaiting_context`（调用核也是同一个 `invoke_child_workflow`）。
    父行挂起时这份上下文经 `execution_tree.bubble_up` 落进父执行的 `pauseState`，成为一个
    `reason=CHILD_APPROVAL` 的 `PendingApproval`：结论落点坐标（`targetExecutionId`/`targetNodeId`）
    与 `kind` 只存服务端，对外只剩 `pendingApprovals` 里的一条（`approvalToken` + `title` +
    `editableFields`）。子侧那份清单取不到时（老数据）退回「只说在等谁」的形，`editableFields`
    仍是空数组而不是 undefined（审批表单按这个键取数）。
    - **刻意不带 `pauseScope` 与 `approvers`**：那是子执行自己的内部语义，对父侧审批没意义。
      身份校验在结论落到子执行之后按子图那份上下文做（`verify_approver` 用的是子执行自己的
      graph 与 inputs），所以父侧丢掉它不会少一道门，只会少一行用户看不懂的「审批人：」（但
      身份本身得从父流传下去，否则子流一配审批人父侧就审不了，见 §11-13）。
    - 提交与续跑：`resolve_submit` 按登记的落点把一轮结论分成「落在本级」与「转给子级」两份
      （`SubmitPlan.forwards`；两者混在一批 → 400，让用户先只提交子流程那份）；`_forward_runs`
      逐跳调 `resolve_child_submit`，父行保持 PAUSED 不动，转发成功后 `_mark_delivered` 把这几份
      凭据标成「已交给这条子执行」。子执行一进终态，`wake_parent_execution` 把父节点从 AWAITING
      退回 PENDING、代次 +1 推起父行。多层嵌套每一跳同构（提交目标永远是本节点的直接子执行）。
    - 可编辑清单因此**放开开始入参**（修正 §5）：父流传给子流的那批参数正是子流的开始入参，
      剔掉它父侧只能看到一个空表单。仍只剔除上游审批的结论键。
    - **文案口径**：父侧审批面板只说一句「子工作流「X」需要你审批（审批环节：Y）」，不得出现
      「子执行 / 提交目标 / 终态 / 去对话页面 / 本节点会自动取回结果」这类开发词与多余引导 ——
      用户执行的是父流、也不是开发者，审批就在父流这一侧完成，不需要理解嵌套执行的转发链路。
      不同意时的提示也分两种场景：子流场景不谈父图的分支走法（`review` 落在子图那道审批上）。
    - 回归：`test/_check_subworkflow_node.py`、`test/_check_execution_tree.py`（冒泡与唤醒两段）、
      `test/_check_approval_passthrough.py` 案 6)/13)（开始入参进可编辑清单）。
13. **开始节点「审批入参」（`type=APPROVER`）的三个口径**（用户现场：父流调起子流后，面板上
    那一行是 `value: null` 的文本框，提交回去就成了字符串）：
    - **值永远是数组**：`_editable_items` 对这一行特殊处理 —— 没传/没默认值时下发 `[]` 而不是
      `null`，并带上 `fieldType: "APPROVER"`。审批面板（`ApprovalPanel.vue`）按这个标记换成标签式
      控件（与运行表单 `DynamicInputForm.vue` 同款），提交时只回传数组；下发值与提交值走同一份
      归一（`toApproverList`），否则「未改动」会被数字/字符串差异误判。普通开始入参不带这个键。
    - **身份跟着嵌套往下传**：`subworkflow_nodes.parent_approver_identity` 从**父图开始节点的
      审批入参**取值，随 `run_child(approver_identity=...)` 下去，再由 `seed_approver_identity` 按
      子图字段名补进子执行的 `inputs`（【工作流】节点与 LLM 内嵌子流工具同一条核，两条入口同口径）。
      子流没这个字段、或调用方已显式传了 → 不覆盖不动作；**父流那份是空也不写，刻意不回落到
      登录用户/api-key 归属人**（直接走 API 调用的父流可能压根没有登录态，凭空造一个身份等于
      把子流的权限校验变成走过场）。推论：**子流配了审批人时，父流自己也要在开始节点声明
      审批入参**（或在审批面板里当场填，见下条），否则一提交就是「无审批权限」。
    - **面板里改了就算**：`verify_approver` 读的是 `inputs`，而编辑只回写
      `node_states[开始].output`（且发生在引擎跑起来之后，晚于校验），所以在 `submit_resolver._plan_local` 里
      用 `lift_approver_edits` 把本轮结论的 `fieldValues` 里「开始节点 + 审批入参 + 无子路径」那一条抬进本轮
      `inputs` 再校验（抬升后的 `inputs` 会随 `claim_for_run` 落库）。只认整值：带子路径的编辑不算
      身份。不同意时面板不展示编辑行（也不发 `fieldValues`），身份靠上一条的自动带入。
    - 回归：`test/_check_approval_passthrough.py` 案 23)-26)，`test/_check_subworkflow_node.py` 案 16)。
14. **对话面自动跟随输出统一到一个 composable**：`views/wemirr/ai/shared/composables/useFollowBottom.ts`
    （模型对话页 `ChatMessages.vue` 与工作流对话弹窗 `WorkflowChatModal.vue` 共用）。弹窗里原先那份
    内联实现（`watch` 数据 + `el.scrollTop = el.scrollHeight`）不成立：全局
    `html { scroll-behavior: smooth }` 会**继承**到滚动容器，程序贴底变成一段动画，动画期间的
    `@scroll` 把「是否贴底」判成 false，跟随就自己关掉了。两条口径：容器显式写 `scroll-behavior: auto`
    且滚动带 `behavior: 'instant'`；观察器盯**内容元素尺寸**（ResizeObserver）而不是数据长度，流式
    追加时每帧用 rAF 合并一次贴底。用户往上滚离底部（阈值内除外）即停止跟随。
15. **「父等子」这段窗口里，页面不得把已交出的结论再问一遍**（用户现场：父流卡在子流程审批 → 面板上
    点了同意 → 页面又弹出一份一模一样的审批，而查库里父子执行都已是 COMPLETED）：引擎没有跑
    第二遍，问题全在订阅侧 —— 结论转出去的那段窗口里父行**故意**仍是 `PAUSED`（要等子执行终态的
    唤醒钩子把它推起），当时订阅端只按行状态分流一次，于是走 DB 回放分支，把上一轮的挂起镜像
    当现况发回 `node.paused` + `workflow.paused`，前端据此又填一遍待决面板；更糟的是那条回放流发完
    就结束，父执行真正的续跑事件这个订阅者一个字也收不到。现口径：
    - **不再另立一份「已转发」副本**：`pauseState` 里那条登记的 `answered` + `deliveredTo` 就是全部
      事实（转发成功后 `_mark_delivered` 写；父行已不在 `PAUSED` 就不写）。`public_approvals()` 只放行
      `is_open` 的项，所以转出去的审批**不会**再出现在详情的 `pendingApprovals` 里。
    - **订阅端只看两份事实**（`event_frames.stream_frames`）：行状态、`WatchState.awaiting_resume`
      （= `PAUSED` 且没有还欠人答的审批，本轮只欠一次唤醒）。非运行态先补历史再接频道，且**只在
      `awaiting_resume` 时才接**（还欠人答与已收尾都不接——worker 那轮已经结束，挂上去只会死等到超时）；
      等待有界（`PARENT_RESUME_WINDOW_S` = 300s）且**到点不发任何帧**，收尾由调用方按当时的行状态定
      （只有调用方知道这次静默是「没人接着跑」还是「还在等子执行」）。过期判定只剩代次一条。
    - **回放与实时同形，不为转发发明新帧**：「还欠人答」与「结论已交出去」两种行发回的事件一模一样，
      差别只在详情的 `pendingApprovals` 空不空——帧不承载审批内容，也就 replay 不出一份能被重复填写的表单。
    - **重复答复在判定层拦**：`PauseState.find()` 只认还没答的凭据，答过的取不到 → `resolve_submit` 给
      `duplicated` 计划，`_run_plan` 什么都不做，只回 200「该审批已提交，请勿重复审批」+ 当前
      `pendingApprovals`。并行下「本流程审批 + 子流程审批」混着提交仍由 `_plan_continue` 那条 400 拦住。
    - **前端只有一个问法**：`probeExecution` 见 `PAUSED` 就读详情的 `pendingApprovals`，为空即
      `'alive'`（接着探、`reconcile` 重新订阅，上限 `MAX_RESUBSCRIBE` = 20 次，收到任意一帧即复位），
      非空才收尾弹面板。
    - 同期修掉一个老 bug：`event_pubsub` 的空读窗口写成 `SUBSCRIBE_IDLE_MS = 5000`，而这个值被直接
      当秒传给 `get_message(timeout=)`，兜底要 83 分钟才走得到一次（等于没有），现为
      `SUBSCRIBE_IDLE_S = 5.0`。
    - 回归：`test/_check_forwarded_approval.py`（结论落点 / 投递标记的四条边界 / 欠不欠人答的唯一读法 /
      回放与分流 / 契约键名）、`test/_check_generation_expiry.py`（过期帧判定）、
      `test/_check_submit_contract.py`（幂等与重复答复）。改了服务端与 worker 侧读到的行内容，
      **必须重启 arq 的 WORKFLOW worker**、前端重新构建才生效。
