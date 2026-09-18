# 工作流「审批节点 + 指定 executionId 再提交 + 大模型记忆」设计冻结

> 本文是需求评审后的**唯一实现口径**，开发按 WBS 步骤逐条落地，代码与本文件冲突时以本文件为准。
> 涉及需求：① 大模型节点记忆配置 ② APPROVAL 审批节点 ③ 指定 execution-id 的异步/同步再提交接口。

## 1. 被废弃的旧能力（先删后建）

| 旧能力 | 处置 | 原因 |
|---|---|---|
| 外部接口触发暂停 `POST /{id}/pause` | 删除 | 与新「审批节点硬暂停」语义冲突，暂停改由审批节点唯一驱动 |
| `POST /{id}/resume`（含携带 edit 变量） | 删除 | 由 `POST /{id}/submit` 的 CONTINUE 模式承接 |
| `POST /{id}/resume-from-snapshot`、`PUT /{id}/variables/{name}` | 删除 | 前置条件都是「外部暂停态」；恢复改输入由 submit 接口 `inputs` 承接 |
| 断点 `breakpoints` / 条件断点 `breakpointConditions` | 删除（前后端全链路） | 断点暂停依赖 resume，resume 已废；调试改由「审批节点 + 重新提交」覆盖 |
| arq 任务 `resume_workflow` | 删除 | 同上 |
| `GET /{id}/snapshot` | 保留 | 只读排障 |

`tb_workflow_execution.breakpoints` 列保留（不清数据），仅停止读写。

## 2. 状态机（execution 级）

```
                            ┌──────────────┐
 execute-async ──► RUNNING ──►│  COMPLETED   │──┐
                    │  ▲      └──────────────┘  │ submit(RETRY)
                    │  │      ┌──────────────┐  ├──────────────► RUNNING
                    │  ├──────│   FAILED     │──┤
        审批节点暂停  │  │      └──────────────┘  │
                    ▼  │      ┌──────────────┐  │
                PAUSED ─┘      │  CANCELLED   │──┘
              (等待审批)        └──────────────┘
                    │ submit(CONTINUE)
                    └──────────────────────────► RUNNING
```

- **PAUSED 的唯一来源**：画布里的审批节点本轮未拿到审批结论。不再有「用户手动暂停」。
- **终态判定**（决定能否再提交）：`COMPLETED / FAILED / CANCELLED`；`PAUSED` 是「可提交的等待态」；`RUNNING / PENDING` 禁止提交。
- 提交前置校验：状态非终态且非 PAUSED → 报「上次任务未结束，请结束后再提交，或取消任务」。
- `TIMEOUT` 不作为 execution 级状态（它只是节点级并行砍分支的终态）。
- **不再有双并发**：同一 execution_id 同时最多一个非终态实例，靠 DB 的 CAS 更新保证。

## 3. 提交模式（新列 `submit_mode`）

| 值 | 含义 | 允许的前置状态 | 调度行为 |
|---|---|---|---|
| `RETRY` | 重新执行 | COMPLETED / FAILED / CANCELLED / PAUSED | 从 START **全部重跑**，不跳过任何节点 |
| `CONTINUE` | 暂停后恢复 | PAUSED | 从 START 遍历，命中「已 COMPLETED」的节点 **跳过（skip）**，到审批节点用本轮审批结论覆写并继续 |

**skip 判据（精确）**：`node_states[nid].status == COMPLETED` 才跳过；`FAILED/CANCELLED/TIMEOUT/RUNNING/AWAITING` 一律重跑。
审批节点特例：状态为 `AWAITING` 必然重跑；已是 `COMPLETED`（往轮已决策）按普通节点跳过，**不重复审批**。

**全流暂停时**（审批节点配置 `pauseScope=ALL`）：正在跑的其他分支被砍，节点停在 `RUNNING` 快照 → 下轮按上面的判据自动重跑，符合预期。

## 4. 事件契约

| 事件 | 触发点 | 新增字段 |
|---|---|---|
| `node.paused` | 审批节点进入等待（新） | `nodeId`、`approvalContext`（可编辑输入项+审批人配置） |
| `workflow.paused` | `run()` 收尾存在未决策审批节点 | `awaitingNodeIds`、`approvalContext`（原来只有 `nodeId/variables`） |
| `node.started` / `node.completed` | skip 节点也发（新） | `skip: true`；skip 的 completed **不带 output、不计耗时** |
| `node.cancelled` | 审批不同意砍下游 / 并行短路 | 审批场景**不带 input/output**，带 `reason` |

- 暂停类事件（`node.paused`/`workflow.paused`）在前端一律按「本轮结束」收敛，停 spinner、停轮询。
- 前端事件白名单需补 `node.paused`（当前只有 `workflow.paused`/`workflow.resumed`）。
- `workflow.resumed` 保留语义：CONTINUE 提交成功后由 worker 首发一帧（替代 workflow.started 之外的重复）。

## 5. 审批节点（APPROVAL）

- 分类：`control`（前端面板显示「业务逻辑」）。
- 出口：**单出口**。同意→正常路由下游；不同意→下游可达闭包全部 `CANCELLED`（复用 `_cancelled_nodes` + `_settle_branch_nodes`），**其他分支照常跑完**，工作流终态 `COMPLETED`（部分取消）。
- 输出：`review`（bool）+ `reviewOpinion` + `reviewBy` + 透传（回写后的）上游数据。
- 配置项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `approvers` | `array<number\|string>` | 允许审批的标识集合；留空=持有 api-key 且知道 executionId 者皆可 |
| `pauseScope` | `ALL` \| `DOWNSTREAM` | 暂停范围：整条流（砍在跑分支）/ 仅本节点及下游 |
| `rejectReply` | string | 拒绝回复文案，下游 END 被砍时兜底作为工作流输出 |
| `timeoutHours` | number(0=不限) | 预留字段，一期不做自动裁决 |

- **审批人识别（开始节点「审批入参」）**：画布存在 APPROVAL 节点时，START 节点才允许添加一个 `type=APPROVER` 的输入字段（值为数组，元素 number|string）。提交时 `inputs[该字段]` 与审批节点 `approvers` 求交集；无交集 → 拒绝提交并提示无权限；`approvers` 为空则跳过校验。`reviewBy` 落库取该入参首个值，缺省时回落 login_user/token。
- 编辑回写：表单按 `(源节点id, 变量名, 当前值)` 三元组渲染；提交时同时改 `node_states[源].output`、`ctx.node_outputs[源]`，并在审批节点 state 记 `reviewDiff`。
- **禁止**放入 LOOP / ITERATION / PARALLEL 子图（graph.validate ERROR）。
- 阻塞只发生在**节点边界**：审批节点不落 `execute()` 内部等待，`run()` 直接以 PAUSED 返回，不占 worker 任务槽、不受节点超时约束。

## 6. 可恢复状态从哪来

1. **权威源 = `node_states`**（每节点 `input/output/branch/llmMessages/review*`），不再依赖 `variables` 快照才能恢复。
2. `ctx` 重建：`inputs` ← 行 `inputs`；`node_outputs` ← `node_states[nid].output`；`executed`/`order` ← 按 order 排序；`global_vars` ← 按 order 重放 `VARIABLE_ASSIGNER` 的 output（LOOP 需把循环计数变量一并写进 output 才能重放）。
3. `_completed_with_branch` ← `node_states[nid].branch`（**新增字段**，`to_dict`/落库/详情回填同步）。
4. `variables` 列瘦身为**唯一一份** snapshot（去掉 `global`/`context` 三份冗余），仅在 PAUSED 与终态写；`RETRY`/`CONTINUE` 优先用 node_states 重建，variables 只作排障与兼容。
5. 图一致性：执行时记 `graph_hash`（节点 id 集合 + 边集合的 sha1 前 16 位），再提交时不一致 → 拒绝并提示「画布已变更，请重新执行工作流」。DEBUG 预览运行同样受此保护（草稿会变）。
6. 落库全部改为 `await` 串行，不再 `asyncio.create_task` 火忘（避免上一轮迟到写覆盖新一轮数据）。

## 7. 统计与明细口径

| 项 | RETRY | CONTINUE |
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
- 多轮对话：`execution_id` 即会话 id —— 对话页/预览页复用同一 execution_id 走 `RETRY` 提交，记忆因此天然跨轮。

## 9. 新接口

```
POST /api/workflow/workflow-executions/{execution_id}/submit
     body: {mode, inputs?, approval?: {nodeId?, approved, opinion?, edits?: [{nodeId,varName,value}]}}
     query: retry_times: Option[int] = 3        → 异步投递，返回 executionId
WS   /api/workflow/workflow-executions/{execution_id}/submit-sync（同 body，事件走该连接）
POST /api/workflow/workflow-executions/{execution_id}/cancel   （保留，前端补按钮 + 文档说明）
```

投递：`job_id = execution_id`（RETRY/CONTINUE 共用，非终态唯一性已保证不与在跑任务同名）；
被 arq 拒绝时按 `retry_times` 每次 sleep 1s 重试；仍失败 → 回滚状态为提交前值，返回「暂时无法重新提交，任务尚未彻底结束，请稍后再试」。

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
4. **断点列与求值入口**：`tb_workflow_execution.breakpoints` 列保留但停止读写（不改表，历史数据可
   回溯）；`comparators.py` 里的自由文本条件求值（`{{ref}}` 替换 + JS 运算符归一 + `eval`）唯一调用方
   是断点条件，随之整体删除 —— 留着一个 eval 入口只会被新功能误用。
5. **RETRY 额外清 `branch`**（§7 未列）：全量重跑不沿用上轮出边端口，且详情页会拿它渲染「分支 x」
   标签，留着就是未跑节点的假路由。
6. **`workflow.resumed` 事件**（§4 未列）：CONTINUE 提交后的首帧（与 `workflow.started` 二选一，两条
   都发会让订阅方把「恢复」当成新一轮执行重置），前端据此复位气泡里的待审批标记。
7. **状态字面量约定**：引擎侧节点态 `AWAITING` / execution 级 `PAUSED`；前端 trace 与画布统一用
   `awaiting`（曾出现 `waiting`/`awaiting` 混用，已全量改为 `awaiting`；`highlightNode` 入参仍用
   `paused`，由 `DebugPanel.toCanvasStatus` 做一层映射）。
8. **审批入口三处**：预览运行面板（PreviewRunner，走 submit-sync WS）、对话气泡
   （WorkflowChatModal，走 `/submit` + 重新订阅同一 executionId）、编辑页工具栏（仅引导到预览面板，
   不重复做一份表单）。
