# 工作流执行契约与内部设计（重构目标态）

> 本文规定「执行 / 提交 / 审批（含多层嵌套审批）」的目标形态：对外契约、内部模块、数据落点、注释规范。
> 代码与本文冲突时以本文为准；**要加任何对外字段，必须先改本文第 1 节的契约表**。
> 历史与需求评审结论见 `docs/workflow-approval-memory.md`（§2 状态机、§5 审批节点、§11 实现补记）。本文不重复记录需求来由。

## 0. 四条不可违背

1. **对外只有三个概念**：`executionId`（一条执行，首次 submit 不给即新建）、`pendingApprovals`（当前欠的审批）、`submit`（唯一入口的唯一动作）。对外字段不得出现内部坐标：子执行 id、子图节点 id、暂停范围、审批人清单、代次之外的计数器。
2. **不做旧口径保留**：被本次重构取代的端点、方法、入参字段、注释一律直接删除，不留 `@deprecated`、不留薄壳转发、不分两期上线（前端与 API Key 说明同批改完）。
3. **每个事实只有一个 owner、一份副本**。禁止往 `tb_workflow_execution.variables` 顶层写野键；跨轮状态只能经 `RoundRequest` / `PauseState` 两个入口读写。
4. **每个施工阶段以「删掉的代码」为验收**：净新增行数 ≥ 0 不通过；且阶段回归必须断言被废弃的符号已不存在（防止留着旧路径双轨跑）。

## 1. 对外契约

### 1.1 概念

| 概念 | 含义 | 生命周期 |
|---|---|---|
| `executionId` | 一次执行的全部身份：首跑、重跑、续跑、审批都挂它 | 首次 submit 不带它时由服务端新建并返回；此后终身不变 |
| `pauseGeneration` | 这条执行第几次开跑（首跑与每次唤醒各算一次） | `claim_for_run` / `wake_to_running` 时 +1；**挂起不推进**（同一次运行发出的帧必须同代次，否则收尾的挂起帧会被自己判成过期） |
| `approvalToken` | **一份待审批的一次性凭据** | 随挂起产生；答完、被新挂起取代、或执行取消即失效 |

### 1.2 端点清单（终版，只有这些）

路由前缀不变（`/workflow-executions`）；`/submit` 是固定段，声明顺序必须在 `/{execution_id}` 动态段之前。

| 端点 | 用途 | 被删除的旧端点 |
|---|---|---|
| `POST /workflow-executions/submit` | **唯一提交入口**：不带 `executionId` = 新建会话执行；带了 = 对该会话的续跑 / 重跑 / 答审批 / 重开 | `POST /workflows/{id}/execute-async`、`POST /{eid}/submit-async` |
| `WS /workflow-executions/submit` | 同上，事件回推本连接（预览页同步驱动；同一 body） | `WS /workflows/{id}/execute-sync`、`WS /{eid}/submit-sync` |
| `GET /workflow-executions/{execution_id}` | 查现状：状态 + `pauseGeneration` + `pendingApprovals` | 响应形状变更（见 1.3） |
| `GET /workflow-executions/{execution_id}/subscribe` | SSE 事件流 | 每帧新增 `pauseGeneration` |
| `POST /workflow-executions/{execution_id}/cancel` | 取消；未答的 `approvalToken` 全部失效 | — |
| `GET /workflow-executions/{execution_id}/snapshot` | 排障只读 | **内部坐标只在这里出现** |
| `GET /workflow-executions/page`、`GET /workflow-executions/{execution_id}/node-executions` | 执行列表与节点明细（只读） | 不变 |

**API Key 的能力边界**（网关按路径形态放行，其余 403）：只有 `POST /submit`、`GET /{eid}`、`GET /{eid}/subscribe`、`POST /{eid}/cancel` 四条；`page` / `node-executions` / `snapshot` 只认登录态。详情必须在内——帧不承载审批内容，`approvalToken` 只有这一个出处，关掉详情就等于第三方答不了审批；列表不能进，一次能拉走别人的执行。
带 `executionId` 的三条由下游校「key 绑定的工作流 == 这条执行所属的工作流」（网关没有 DB，判不了归属）；新建执行的 `workflowId` 由 key 决定，调用方传了就必须一致。

### 1.3 统一入参与统一出参

**入参类**（四种意图共用一个类，顶层只允许这 5 个字段）

```python
class ApprovalDecisionReq(BaseModel):
    """一份审批结论；凭哪份待办答、答什么、改了哪些字段。"""
    approvalToken: str = Field(..., description='pendingApprovals[].approvalToken')
    action: Literal['APPROVE', 'REJECT'] = Field(..., description='同意 / 不同意；两者都继续走下游')
    fieldValues: dict = Field(default_factory=dict, description='键 = editableFields[].name，值 = 改后的完整值')
    opinion: Optional[str] = Field(None, max_length=2000, description='审批意见')


class WorkflowSubmitReq(BaseModel):
    """submit 的唯一入参；提交意图全由「给了哪几个键」表达，不出现 mode。"""
    executionId: Optional[str] = Field(None, description='不传=新建会话；传=对已有会话的任一后续动作')
    workflowId: Optional[int] = Field(None, description='新建时必填；传了 executionId 时可省')
    values: Optional[dict] = Field(None, description='业务输入；不传沿用上轮 inputs')
    decisions: Optional[list[ApprovalDecisionReq]] = Field(None, description='审批结论；有未答审批时的唯一推进方式')
    restart: bool = Field(False, description='放弃未答审批并全量重跑；仅带 executionId 时有意义')
```

被删除的旧入参：`WorkflowExecutionReq`、`WorkflowSubmitReq.mode`、`WorkflowSubmitReq.inputs`（改名为 `values`）、`ApprovalDecisionReq.nodeId`（改由 `approvalToken` 定位）、`ApprovalDecisionReq.approved`（改为 `action`）、`ApprovalDecisionReq.edits`（改为 `fieldValues`）、`ApprovalEditReq` 整类（ nodeId/varName/path 三坐标不再由调用方提供）。

**入参校验矩阵**（唯一收口在 `execution/submit_resolver.py`，不符合就 400 + 可读提示）

| 组合 | 判定 |
|---|---|
| 无 `executionId` 且无 `workflowId` | 400「新建执行必须传 workflowId」 |
| 无 `executionId` 但给了 `decisions` 或 `restart` | 400「新执行没有可答复的审批」 |
| 有 `executionId` 且 `workflowId` 与库中不符 | 400「该执行不归属于此工作流」 |
| `decisions` 与 `values` 同时给 | 400「审批结论与新一轮输入不能同时提交」 |
| 有未答审批但只给了 `values` | 400「存在未答复的审批，请先答复或传 restart 重开」 |
| `fieldValues` 里的字段名不在这份待办的清单内 | 400「字段 X 不属于该审批节点可编辑范围」 |
| `decisions` 里同一 `approvalToken` 出现两次 | 400「同一份审批不能在本请求内重复答复」 |

**出参类**（四种意图同形，调用方不需按分支解析）

```python
class SubmitResult(BaseModel):
    """submit 的唯一返回体。"""
    executionId: str
    status: str            # RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED
    pauseGeneration: int   # 本次提交后的当前代次
    duplicated: bool = False  # 仅「重复答同一份凭据」时为 true
    pendingApprovals: list[dict] = []  # 提交后仍欠的审批；无则空数组
```

> 变更点：`execute-async` 原来返回**裸 executionId 字符串**，现统一为 `SubmitResult` 对象（前端与 API Key 说明同批改）。

**首跑与后续动作的全部合法请求体**

```text
POST /workflow-executions/submit

新建会话  {"workflowId":2,"values":{"query":"用户电价数据是多少？"}}
答审批    {"executionId":"e9f1","decisions":[{"approvalToken":"c9f3a1","action":"APPROVE",
                                             "fieldValues":{"who":[123]},"opinion":"同意"}]}
追问一轮  {"executionId":"e9f1","values":{"query":"那 9 月的呢？"}}
重开一轮  {"executionId":"e9f1","restart":true,"values":{...}}
原样重跑  {"executionId":"e9f1"}

→ 202 {"code":200,"data":{"executionId":"e9f1","status":"RUNNING",
                         "pauseGeneration":2,"pendingApprovals":[]}}
→ 200 {"code":200,"message":"该审批已提交，请勿重复审批",
        "data":{"executionId":"e9f1","status":"PAUSED","pauseGeneration":8,
                "duplicated":true,"pendingApprovals":[ ...当前还欠的... ]}}
```

**查现状**

```text
GET /workflow-executions/e9f1
{"code":200,"data":{
  "executionId":"e9f1","workflowId":2,"workflowName":"父流",
  "status":"PAUSED","pauseGeneration":7,
  "inputs":{...},"outputs":null,"errorMessage":null,"duration":6906,
  "pendingApprovals":[
    {"approvalToken":"c9f3a1","nodeId":"8c002f61","nodeLabel":"大模型",
     "title":"子工作流「查询电价数据」需要你审批","approvalNodeLabel":"审批",
     "editableFields":[
       {"name":"who","label":"审批人","sourceNodeLabel":"开始",
        "valueType":"APPROVER","value":[],"required":false},
       {"name":"query","label":"query","sourceNodeLabel":"开始",
        "valueType":"STRING","value":"project_id: ... 用户电价数据是多少？","required":true}],
     "allowedActions":["APPROVE","REJECT"]}],
  "nodeStates":{...}        // 详情页渲染用，保留现状形状
}}
```

相对现状的删改：`approvalContext`、`awaitingNodeIds`、`awaitingForwarded` 三个字段合并为 `pendingApprovals`（它们是同一件事的三份副本）；`childExecutionId`/`approvalExecutionId`/`approvalNodeId`/`approvers`/`pauseScope`/`kind` 不再外发。

`editableFields[].name` 是**审批节点透传给下游的输出键名**，因此一份清单内天然唯一——它就是回传时的主键。`sourceNodeLabel` 与 `nodeId` 只用于展示与画布高亮，**不参与提交**。

**事件流**：每帧 payload 带 `executionId` 与 `pauseGeneration`；`node.paused`/`workflow.paused` 表示本轮结束，其余帧沿用现状（`node.started`/`node.delta`/`node.completed`/`workflow.resumed`/终态事件）。**帧上不承载任何审批内容**（待办清单、可编辑字段、暂停范围、审批人都不在帧里）：一份「此刻欠谁」只存 `pauseState` 一份，要看就去 `GET /{executionId}` 取 `pendingApprovals`——帧只负责把页面推到「该看详情了」。

晚订阅者靠回放补看：回放按行与节点状态无条件补帧——`node_states` 里是 `AWAITING` 的节点补一帧 `node.paused`，行是 `PAUSED` 就以 `workflow.paused` 收尾。「还欠人答」与「结论已交给子执行」两种行回放**一模一样**（差别只在详情的 `pendingApprovals` 空不空），订阅端因此不需要为转发单独发明一种帧。

### 1.4 mode 推导表

调用方不传 mode。全项目唯一一处判定在 `execution/submit_resolver.py::resolve_submit`：

| 当前事实 | 请求给了什么 | 判定 |
|---|---|---|
| 无 `executionId`（新建） | `workflowId` | 新建首跑（NEW）：建执行行后代次从 1 起，全量跑 |
| 有未答审批 | `decisions` | CONTINUE（未答全则只推进已答项，其余继续挂起） |
| 有未答审批 | `restart` | RETRY，作废未答项 |
| 无未答审批（终态） | `values` 或空 | RETRY |
| RUNNING / PENDING | 任意 | 409「上一条还在执行中，可等待或取消」 |

`restart` 缺省为 false；`values` 缺省沿用行内 `inputs`。NEW 与 RETRY 在执行行上的差别只有：前者插一行、后者复用原行并清残留。

### 1.5 错误与幂等

| 场景 | 响应 |
|---|---|
| 凭据已答过 / 已被新挂起取代 / 执行已取消 | **200 `message:"该审批已提交，请勿重复审批"` + `duplicated:true`** + 当前 `pendingApprovals`；后台**不执行任何提交动作**（该行为被视作禁止，不当幂等成功处理） |
| 状态 RUNNING / PENDING | 409 + 可读文案 |
| 入参不满足 1.3 校验矩阵任一行 | 400 + 指明是哪个键的组合 |
| 画布已变更（`graph_hash` 不一致） | 400「画布已变更，请重新执行工作流」 |
| 审批人不符 | 400「无审批权限：输入的审批人不符合工作流审批节点的要求」 |
| 投递被队列拒绝且重试用尽 | 400 + 状态回滚（现状口径不变） |
| 并发提交同一条执行（状态 CAS 抢输） | 409「他人正在提交，请勿重复提交相同任务」 |

幂等只针对审批凭据且只回 200；新建会话与重跑的互斥仍由状态 CAS 保证（抢输方 409）。这类拒绝回执两条传输同形状：业务码 + 面向调用方的原文案——它们没开始跑，不是服务异常，不该占 500。

### 1.6 调用方视角的完整链路

```text
POST /workflow-executions/submit {"workflowId":2,"values":{...}}
   └─► executionId = e9f1（此后终身只有这一个 id，也不再换入口）
   │
   ├─ GET /e9f1/subscribe ──► 事件流（token 流、暂停、续跑、终态，一条流看到底）
   ├─ GET /e9f1            ──► status + pauseGeneration + pendingApprovals
   │
   ├─ 需要人 → POST /submit {"executionId":"e9f1","decisions":[{approvalToken,action,fieldValues}]}
   ├─ 要追问 → POST /submit {"executionId":"e9f1","values":{...}}
   ├─ 想重开 → POST /submit {"executionId":"e9f1","restart":true}
   └─ 回到 subscribe / GET —— 嵌套几层都是这一套，调用方看不到「子执行」这个词
```

预览页只换个传输：同一个 body 发到 `WS /workflow-executions/submit`，事件从本连接回来。

## 2. 关键设计说明

### 2.1 `approvalToken` 是一次性凭据，不是身份

它由服务端随机生成、记在 `PauseState` 里，与「这条待办当前指向哪条执行的哪道审批、当时那份数据是什么」绑在一起。三个理由使它不能换成 `{执行}:{代次}:{节点}` 这类拼插串：

1. **拼插串在嵌套下不唯一**：父停在第 N 代的某节点，结论转给子后子又卡在孙的审批——父的执行 id、代次、节点 id 三者都没变，但已经是另一道题。必须由服务端换号来表达「旧的那份作废了」。
2. 拼插串把内部坐标写进了对外契约，调用方会去解析它。
3. 一次性凭据让幂等与过期判定完全落在服务端，提交体里连代次都不用带。

配套约定：`approvalToken` 解析结果一律打日志（`approvalToken=c9f3a1 → exec=96d3 node=0310e7`），因为详情响应里 `approvalToken` 与 `title`/`nodeLabel` 并排给出，人看图对应、程序凭号回话。

### 2.2 `fieldValues` 为什么按字段名回传

调用方写的是面板上看到的字段名，服务端凭 `approvalToken` 找到该字段登记的 `(源节点, 变量名, 子路径)` 再回写。于是 `nodeId`/`varName`/`path` 三坐标从契约消失；`path` 不再需要调用方声明，也就再也漏不了——历史上「`path` 未声明被静默丢掉、key 级编辑退化成整值覆盖」那一类事故从接口面上被封死。

### 2.3 `pauseGeneration` 只解决一件事：过期帧

它是**内部事实**，对外仅在详情与事件帧里出现，供消费方判断「这条帧是不是当轮的」。规则一条：帧的 `pauseGeneration` **低于**本次订阅认的代次 → 不发。低于它的是上一轮迟到的帧，等于它的是本轮，**高于**它的是被推起之后的新轮（必须照转，否则父等子的续跑会在同一条流上断掉）；没带这个键的帧一律不丢（worker 未重启时不能把整条流滤空）。这条规则取代了现状订阅端的三处特判（`skip_pause_frames`、`got_terminal`、`forwardedApprovals`）。**不拿它当幂等键**，幂等归 `approvalToken`。

### 2.4 为什么保留一个 executionId（会话 / 运行 / 可恢复状态合一）

这是本项目的既有选择（`docs/workflow-approval-memory.md` §8：execution_id 即会话 id，对话页复用同一 id 重跑，记忆因此跨轮累积），本文维持不变。代价要说清：`node_states` 必须当跨轮权威源、RETRY 必须清残留、`round` 与 `pauseGeneration` 是两个计数器。因此**职责锁死**：`round` 只服务记忆排序与节点明细分轮，`pauseGeneration` 只服务事件过期，任何新逻辑不得复用前者判新旧。

### 2.5 全景图：一个请求从进来到落库经过谁

```
调用方        API 进程（无状态：鉴权 + 编排，不跑图）        arq worker 进程（唯一跑图的人）        MySQL 一行
  │
  ├─POST /submit {workflowId:2, values:{…}}
  │        submit_resolver ──► execution_state.claim_for_run ──► 插一行 status=RUNNING
  │                                                               roundRequest 落库
  │◄─202 {executionId}                                        （投队列，job_id = executionId）
  │                                                              engine 逐节点推进
  ├─GET /{eid}/subscribe ──► event_frames.stream_frames ◄────── 实时帧（Redis 频道）
  │        补当轮历史帧 + 续实时帧；一条过期规则：代次不符即丢
  │
  │                        （跑到审批节点，拿不到结论）◄──────── 节点置 AWAITING
  │                            pause_state.register_from_engine_context ─► 子执行在等则先 execution_tree.bubble_up
  │                            execution_state.save_run_result ─► status=PAUSED + pauseState（代次不动）
  │◄─node.paused / workflow.paused
  │
  ├─GET /{eid} ──► approval_projection ─► {status, pauseGeneration, pendingApprovals[]}
  │
  ├─POST /submit {executionId, decisions:[{approvalToken, action, fieldValues}]}
  │        submit_resolver：推导表 + 凭据幂等（答过 → 200 duplicated）
  │        按每份待办的 target_* 分岔：
  │          本级 → round_request.decisions
  │          子级 → ForwardedRun（子执行走同一条提交）+ 本级 pause_state.mark_delivered
  │        execution_state.wake_to_running（代次 CAS；抢输=已有人在推，直接返回）
  │                                                            worker 续跑（已完成节点跳过）
  │◄─workflow.resumed → node.* → 终态
  └─ 子执行进终态 ─────────────────────────► execution_tree.wake_parent_execution（回到 submit 那一跳）
```

读图要点：**API 进程与 worker 从不调用彼此的函数**，只通过这一行 MySQL 交接；改 `status` 的入口只有 `claim_for_run` / `save_run_result` / `wake_to_running` / `rollback_claim` 四个，推进 `pause_generation` 的只有前一个是抢占、最后一个是唤醒（`save_run_result` 挂起时不动代次）。

### 2.6 三层嵌套：待办怎么冒到父、结论怎么落回孙

```
父 P ─ 停在「子工作流」节点 nc        pauseState[P]: token t1, routing=(C, nc)
  子 C ─ 停在「子工作流」节点 ng      pauseState[C]: token t2, routing=(G, ng)
    孙 G ─ 停在审批节点 ga            pauseState[G]: token t3, routing=(G, ga)  ← 真正等人答的这份

对外只有父 P 这一个 executionId：GET /P → pendingApprovals 只列 t1（title 已是「孙流 X 需要你审批」）

POST /submit {executionId: P, decisions:[{approvalToken: t1, …}]}   ——调用方全程不知道该有 C 和 G 存在
  1 提交 P：落点是 C → claim_for_run(C)      P.mark_delivered(t1→C)
  2 C 续跑到 ng，手里那份待答是 t2 → claim_for_run(G)     C.mark_delivered(t2→G)
  3 G 拿到结论，跑完审批节点，一路到终态
  4 wake_parent_execution(C) → C 的 ng 结果由节点执行器按「子已终态」自取 → C 终态
  5 wake_parent_execution(P) → P 的 nc 同理 → P 续跑到完成
```

每一跳做的动作**完全同构**：换号发新 token → 标 delivered → 推回 RUNNING，靠代次 CAS 保证并发下只有一跳赢。层数增加不新增分支，这是 R4 敢把三个 helper 删掉的依据——转发不是一段专门的逻辑，它就是「拿子执行的 token 再提交一次」。

## 3. 数据落在哪

### 3.1 表结构变更

```text
ALTER TABLE tb_workflow_execution
  ADD COLUMN pause_generation INT NOT NULL DEFAULT 1 COMMENT '挂起代次：第几次开跑（含唤醒），事件过期判定用';
```

存量行不批量 UPDATE（避免与在跑任务争写同一行）：`pause_generation` 靠列默认值 1，`variables` 缺哪段就按空事实读（`decode_variables` 的唯一口径），不留 legacy 分支。

### 3.2 `variables` 列的两段形状

```text
{"roundRequest": {"round":3,"decisions":{"8c002f61":{"approved":true,"opinion":"同意","reviewBy":"u1","edits":[]}}},
 "pauseState":   {"pendingApprovals":[
                    {"approvalToken":"c9f3a1","nodeId":"8c002f61","nodeLabel":"子工作流节点",
                     "reason":"CHILD_APPROVAL",
                     "targetExecutionId":"96d3","targetNodeId":"0310e7",   // 内部落点，不外发
                     "childWorkflowName":"查询电价数据",
                     "allowedActions":["APPROVE","REJECT"],
                     "answered":false,"deliveredTo":"96d3",
                     "fields":[{"name":"who","label":"审批人","valueType":"APPROVER","value":[],
                                "required":false,"sourceNodeLabel":"开始",
                                "sourceNodeId":"29eafe9c","sourceVarName":"who","sourcePath":""}]}]}}
```

`roundRequest` 只装**跑完即弃**的两件事：对话轮次、待喂给审批节点的结论（提交模式与本轮入参各有其列：`submit_mode` / `inputs`）。worker 收尾写回时把结论清空、轮次留下，因此不存在「上轮结论被当本轮已审」。

`pauseState` 里**不带代次**：它是 `pause_generation` 列，重复存一份就会分叉，读取时由 store 从列上带入。

`targetExecutionId` / `targetNodeId` / `deliveredTo` 与 `fields[]` 里的 `source*` 三元组是内部记录，`approval_projection` 只外发白名单字段（字段名见 `EditableField.to_public`）。**顶层不再有第四个键。**`reason` 取 `APPROVAL`（本执行的审批节点在等）或 `CHILD_APPROVAL`（等的是子执行里那道审批）。

### 3.3 一份事实一个写入方

| 事实 | 唯一写入方 | 读取方 |
|---|---|---|
| `status` | `ExecutionStateStore`（抢占 / worker 收尾 / 唤醒 / 投递回滚四处） | 详情、订阅分流、worker 状态守卫 |
| `pause_generation` | `ExecutionStateStore.claim_for_run` 与 `wake_to_running` | 事件帧打戳、订阅过期判定 |
| `node_states` | worker（节点边界与收尾） | 恢复 `hydrate`、详情 |
| `roundRequest` | `claim_for_run` 写全份；`save_run_result` 清掉已消费结论 | worker 重建运行时 |
| `pauseState` | worker 挂起时写全份；API 进程答完后只改 `answered`/`deliveredTo` | 详情、投影、唤醒钩子 |
| `inputs` / `outputs` | API 进程（提交时）、worker（收尾时） | 详情、子执行取数 |
| 审批人身份 | 提交时写 `inputs` 列（`lift_approver_edits` 抬面板改过的那一份） | `verify_approver`（在那条执行上） |

## 4. 内部模块

### 4.1 七个文件的职责边界

新增 `service/service_workflow/execution/` 包，持有「跨轮与等待」的全部事实；`workflow_engine` 只负责图推进，不再关心状态语义。

| 文件 | 唯一职责 | 取代现状 |
|---|---|---|
| `execution_state.py` | 执行行的唯一读写口：状态 CAS、`pause_generation` 自增、两段 JSON 的编解码与读写 | 散在 `_apply_submit`/`_rollback_submit`/`_persist_state`/`_resume_parent_execution`/`_mark_forwarded_awaiting` 里的 UPDATE 与 row 赋值 |
| `round_request.py` | 「本轮要消费什么」：对话轮次 + 待喂给审批节点的结论（跑完即弃） | `_apply_submit` 手工拼的 4 键 variables（其中 nodeStates/submitMode/round 都是别的列的复制） |
| `pause_state.py` | 「此刻在等谁、等什么、哪些已答已投递」+ token 生成与换号 | `awaitingNodeIds`/`approvalContext`/`approvalDecisions`/`forwardedApprovals` 四个野键，外加 row.awaiting_node_id 与它的一对重复 |
| `approval_projection.py` | `PauseState` → 对外 `pendingApprovals`，字段白名单声明式，嵌套时递归复用 | `build_awaiting_context` + `_child_approval_context` 的逐键裁剪裁决 |
| `execution_tree.py` | 父子之间的两跳：子欠的审批往上冒（`bubble_up`）、子收尾后把父叫醒（判该不该 / 推状态分开）。结论往下投不在这里——那是一次普通提交 | `_resume_parent_execution` 70 行手工修补 + 三个转发 helper + `_child_approval_context` 的坐标拼接 |
| `submit_resolver.py` | 1.4 推导表 + 幂等判定 + 结论落点（本级 `decisions` / 子级 `ForwardedRun`） | `_prepare_submit` 的 6 个 if |
| `event_frames.py` | 事件帧读取与按代次过滤，SSE 与 WS 共用 | `_frame_event_type`/`_replay_execution`/`_paused_frame` 及其特判 |

`workflow_execution_service.py` 退化为薄编排层：只做「鉴权 → 调 execution 包 → 拼响应」，**不持有状态语义**。
这条职责边界才是验收口径，行数不是（拼详情与查明细本身就是代码量所在）：执行行状态列的写入只允许出现在
`execution/execution_state.py`，由 `test/_check_state_shape.py` 按正则扫源码守住。引擎侧改动只有三处：
`AwaitingApproval` 携带 `reason`、`_pause_for_human_input`（原 `_enter_awaiting`）发帧带代次、挂起事实交由 service 落库（现有 hook 注入方式不变）。

### 4.2 主要方法签名（同时作为注释规范示范）

```python
# execution/execution_state.py
RUNNING_STATUSES = (STATUS_RUNNING, STATUS_PENDING)


class ExecutionStateStore:
    async def claim_for_run(execution_id, claim: RunClaim) -> int:
        """把一条非运行中的执行抢成本轮要跑的状态，返回新挂起代次。

        写入：status/submit_mode/graph_hash/awaiting_node_id/roundRequest/代次 +1，
        给了 inputs 才写 inputs；RETRY 另归零统计与耗时；同时清上轮节点明细。
        抢占带 status 条件，抢输抛 ClaimLost（文案直接面向调用方，提交路径回 409）。
        """

    async def save_run_result(execution_id, facts: RunResultFacts) -> None:
        """落 worker 本轮结果（节点边界/挂起/终态，全部串行 await）。

        两段式 variables 只在挂起与终态写；写回时本轮结论已消费，只保留轮次。
        """

    async def wake_to_running(execution_id, expected_generation=None) -> int:
        """把这条执行推回 RUNNING 并推进代次，返回新代次；抢不到返回 0。

        代次不符即抢不到：说明已经有人在推，或这条执行已经换过一轮。
        """

    async def mark_cancelled(execution_id) -> bool:
        """把这条执行判成 CANCELLED，返回有没有改到这一行。

        只接 RUNNING / PAUSED（带 status 条件的 UPDATE）：与并发的收尾抢写时
        谁先改到谁算，不会把已经落下的终态盖回取消。
        """

    async def change_pause_state(execution_id, apply_change, only_while_paused=True) -> PauseState | None:
        """在同一事务里改这份挂起事实再写回；回调返回假值即不落库。"""

    async def read_watch_state(execution_id) -> WatchState:
        """取订阅与详情判定要的三份事实：行状态、代次、还欠谁的审批。"""


# execution/pause_state.py
class PauseState:
    def register_from_engine_context(self, context: dict) -> str:
        """登记引擎在节点边界抛出的那份挂起上下文，返回新的 approvalToken。

        同一节点重挂即换号：该节点上一份登记（含已答的）整体丢弃，旧凭据自然失效。
        """

    def find(self, approval_token) -> PendingApproval | None:
        """按凭据取待审批；已答或已换号取不到（调用方据此判重复审批）。"""

    def find_any(self, approval_token) -> PendingApproval | None:
        """不分已答未答都取得着：用来区分「答过了」与「从来没有过这个凭据」。"""

    def open_approvals(self) -> list:
        """还欠人答的项；空即代表本轮可以继续推进。"""

    def mark_delivered(self, approval_token, target_execution_id) -> str:
        """记「结论已交给这条执行」：待审批仍在清单里，但不再接受第二次投递。"""


# execution/execution_tree.py
def bubble_up(child_execution_id, pause: PauseState, node_states, awaiting_node_id) -> dict | None:
    """子执行欠着的那道审批 → 父侧镜像要用的那份上下文（要审的数据 + 提交坐标）。

    提交坐标只指本条子执行（`approvalExecutionId` + 它正挂着的那个节点）：多层嵌套时
    由子执行自己再往下转一跳，校验与审批人判定才能一直落在真有审批节点的那张图上。
    没人欠着时返回 None，父侧退回「只说在等子流程」的形。
    """

async def find_waiting_parent(child_execution_id) -> WaitingParent | None:
    """找出「因这份子执行而挂着」的那条父执行，不动任何状态（唤醒的前置都在这一步）。

    子行未进终态 / 没有父坐标 / 父不在 PAUSED / 父等的不是这一份 / 兄弟子执行还在跑
    ——五种情况都给 None。
    """

async def wake_parent_execution(child_execution_id) -> WaitingParent | None:
    """子执行进终态后，把正等它的父执行推回 RUNNING，返回这条父执行。

    动作只有 `wake_to_running` 那一步：节点状态退回 PENDING（结果由节点执行器按「子已
    终态」分支自取）、清上轮结论、代次 +1。推不动（代次不符）给 None。
    让它真的跑起来是调用方的事：本模块不认识 arq 与 WebSocket。
    """


# execution/event_frames.py
def paused_frame(execution_id, resp) -> str:
    """拼 workflow.paused：本轮已跑完、停在等人。欠着谁不在帧里，去详情看。"""


async def stream_frames(execution_id, *, read_detail, live_frames) -> AsyncIterator[str]:
    """给出这条执行的完整事件流：先补当轮已跑出的历史，再续实时帧，终态或到点即结束。

    :param read_detail: 读这一行的对外详情（service 的 get_execution）
    :param live_frames: 订阅事件频道（service 的 subscribe_event_channel）

    两个读口由 service 注入：本模块不 import services，也不认识 MySQL 与 Redis。
    分流只看两份事实：行状态、本轮是不是只欠一次唤醒。过期判定只有一条：代次低于订阅起点即丢。
    """
```

### 4.3 三条主链路

```text
【A 提交】HTTP/WS → resolve_submit(请求, ExecutionStateStore + PauseState)
                        │ 5 行推导 + 凭据幂等判定；不匹配 → 200 duplicated
                        ▼
                  结论落点（就在 resolve_submit 里：每份待办的 target_* 就是落点）
                        ├─ 目标就是本执行 → RoundRequest.decisions
                        └─ 目标是子执行 → ForwardedRun：子走同一条提交；父 PauseState.mark_delivered
                        ▼
                  ExecutionStateStore.claim_for_run → 投队列（job_id = executionId）→ 失败回滚

【B 执行】worker：Engine.run() → 审批节点无结论 → 节点 AWAITING
                        ├─ 是子执行在等 → 挂起冒泡（bubble_up）→ 父登记新 token 并换号
                        └─ 普通审批 → PauseState.register_from_engine_context
                        ▼
                  save_run_result（落 pauseState，代次不动）→ 发 node.paused / workflow.paused
        子执行终态 → wake_parent_execution → claim_for_run(续跑) → 回到 B

【C 观察】GET 详情 / SSE：读同一份 PauseState 与代次；stream_frames 内一条过期规则。
        帧只说「停下了」，pendingApprovals 只在 GET 里给一份：SSE 负责及时，GET 负责权威。
```

### 4.4 依赖方向（只有这一个方向，违反即评审拦截）

```
routers → services（薄编排：只调 execution 包）→ execution 包 → models / engine
                     ↑                    ↑
        arq_tasks（只调 service.run_in_worker）   engine 不 import execution 包
```

- `workflow_engine` 不认识「审批令牌」「代次」「父子」这些状态语义；挂起事实经**现有 hook 注入**回传给 execution 包落地（方向单一，无环）。
- execution 包内是单向 DAG、不回头：最底层是 `RoundRequest` / `PauseState` 两个数据结构，往上是 `execution_state` 这个唯一读写口，`approval_projection` / `submit_resolver` / `event_frames` / `execution_tree` 都只向下调用。横向只有 `execution_tree → approval_projection` 一处（冒泡要复用同一份对外口径）。出现环即说明职责分错。
- 缝补的成因是「谁都能 row.xxx = …」。收口后：写 `variables` 顶层、写 `status`、写 `pause_generation` 的代码只存在于 `execution_state.py` 一个文件，用回归脚本按正则断言。

## 5. 从现状到目标：删除清单

这些符号在目标态**不存在**（不是废弃、不是转发），回归脚本按名断言：

**端点**：`POST /workflows/{id}/execute-async`、`WS /workflows/{id}/execute-sync`、`POST /{eid}/submit-async`、`WS /{eid}/submit-sync`。

**service 方法**：`execute_async`、`execute_sync`、`submit_async`、`submit_sync`、`_prepare_submit`、`_apply_submit`、`_rollback_submit`、`_mark_forwarded_awaiting`、`_child_target`、`_child_approval_routes`、`_forwarded_req`、`_replay_execution`、`_paused_frame`、`_frame_event_type`、`_execution_brief`、`_resume_parent_execution`、`_child_approval_context`（以上均被 execution 包同职责函数取代）。

**字段与常量**：`WorkflowExecutionReq`、`ApprovalEditReq`、`WorkflowSubmitReq.mode`、`WorkflowSubmitReq.inputs`（→`values`）、`ApprovalDecisionReq.nodeId`/`approved`/`edits`（→`approvalToken`/`action`/`fieldValues`）、`FORWARDED_AWAITING_KEY`、`awaitingForwarded`、`approvalContext`、`awaitingNodeIds`（详情与事件帧的外发层面，`node_states` 列内与引擎 hook 里的同名键不算）、事件帧里的 `approvalContext`（帧不再承载审批内容）、`ExecutionStartedDto`。

**引擎侧**：`_enter_awaiting`（改名 `_pause_for_human_input`）、`restore()` 的三段 legacy 兼容分支、`got_terminal` 与 `skip_pause_frames` 两个判定、`_approval_context()` 里的 `approvers`/`pauseScope`（审批人按图校验、暂停范围随 `AwaitingApproval.scope` 走，两份都不必外发）。

## 6. 注释规范（本次重构的强制验收项）

只重写被触到的文件，不做全项目注释大扫除。

1. 方法第一行写「这个方法做完之后，世界多了什么事实」，动词开头，一句。
2. 之后只允许四类内容：参数取值域、返回值语义（为空意味什么）、不变量与副作用（改了哪几份状态、必须与谁同序）、最多一句「为什么」并指向本文或 memory 文档的锚点。
3. 禁止出现：需求编号、评审来由、用户现场复述、「曾经如何→现在如何」、某字段在哪个页面显示。这些属于 docs，代码里只留 `§x-y`。
4. 硬度量：docstring 中出现的方法名之外的概念标识符 ≤ 1。超标的典型是现状 `_child_approval_routes` 的注释里同时出现 `forwardedApprovals`、`approvalExecutionId`、`pauseScope`——这个数字就是耦合读数。
5. 超过 8 行 = 这个方法在做两件事，拆开而不是解释。
6. 私有 helper（`_x`）签名即文档；需要解释就写在调用点那一行上。
7. 模块头 ≤ 3 行：本模块承载哪个唯一事实 / 谁读 / 谁写。
8. 命名：名词短语说明「是什么事实」，动词短语说明「做完多了什么」；不用 `facts`/`wait`/`surface`/`handle` 这类需要读实现才懂的单词。

## 7. 已知边界（明确不做，防止以后靠特判补）

- 审批超时无兜底：挂起的执行会一直停着，直到有人答或有人取消。`timeoutHours` 字段继续占位。
- 不做通知与待办中心：审批人从对话页/预览页/详情页看到待办；跨流程的统一待办列表不在本期。
- 审批不参与流转控制：不同意也只继续走下游，分流由下游条件节点读 `review` 决定（§11-11 结论不变）。
- 审批人身份不自创：父流没声明审批入参且面板也没填，就是无权限，不回落登录用户（§11-13 结论不变）。
- 会话与运行不分家：仍是同一个 executionId（2.4 的代价照付）。
- 不做旧接口兼容层：四个旧端点直接下线，前端、API Key 用法说明、示例 curl 在同一期改完（见 §8 R3/R5）。

## 8. 施工顺序与验收

| 阶段 | 内容 | 删除项 | 回归 | 对外影响 |
|---|---|---|---|---|
| R1 | 加 `pause_generation` 列与事件字段；建 `pause_state.py`/`event_frames.py`，订阅端改一条过期规则 | 订阅端三处特判、`FORWARDED_AWAITING_KEY` 及其读写 | `_check_generation_expiry.py` | 无（新字段先发） |
| R2 | 建 `execution_state.py`/`round_request.py`，`variables` 改两段式 | `restore()` 三段 legacy 分支、散落的 row 字段赋值 | `_check_state_shape.py` + `_check_pause_state.py` | 无 |
| R3 | 建 `approval_projection.py`/`submit_resolver.py`；`POST|WS /workflow-executions/submit`（带统一入参类与校验矩阵）；详情给 `pendingApprovals`；帧去审批内容；**四个旧端点与对应 service 方法直接删除**；API Key 用法说明重写 | `execute_async`/`execute_sync`/`submit_async`/`submit_sync`/`_prepare_submit` 及其 if、`approvalContext`/`awaitingNodeIds`/`awaitingForwarded`、`WorkflowExecutionReq`/`ApprovalEditReq` | `_check_submit_contract.py`（含旧端点与旧符号不存在的源码级断言） | 旧端点不存，前端必须同步上 |
| R4 | 建 `execution_tree.py`：冒泡（`bubble_up`）与唤醒（`find_waiting_parent` 判该不该 / `wake_parent_execution` 推状态）两段化，token 逐跳换号；结论投递并回 `submit_resolver` 的 `ForwardedRun` | 三个转发 helper、`_resume_parent_execution`、`_child_approval_context`、`lift_approver_edits` | `_check_execution_tree.py`（1/2/3 层冒泡 + 五种不唤醒 + 依赖方向） | 无 |
| R5 | 前端改吃 `pendingApprovals` 与 `approvalToken`；两份文档定稿 | `awaitingForwarded`、前端按 nodeId 反推层级的分支 | vue-tsc + eslint + 手工清单 | 无（R3 已断旧口径） |

R3 是对外契约切换日：旧端点删除、新端点上线、前端发布、API Key 说明重写必须同一批完成，不留过渡期。

每阶段：`py_compile`、`_check_import_loop.py`、本阶段新回归与既有 `_check_approval_passthrough.py`/`_check_subworkflow_node.py` 全绿，才进下一阶段。**后端每阶段上线必须重启 arq 的 WORKFLOW worker**，R5 需前端重新构建。

## 9. 手工复测清单

1. 单层审批：挂起 → 同意（带编辑）→ 续跑 → 完成。
2. 父等子（本轮 bug 场景）：提交后**不再**弹第二份审批，父流续跑事件在同一条 SSE 上不断流。
3. 三层嵌套：父 → 子 → 孙，调用方全程只有一个 executionId，每次拿到的 `approvalToken` 都是新的。
4. 并行两分支各卡一道审批：一次答一项、一次答两项、混着本流程审批答（应拦住）。
5. 预览页 WS 路径与对话页 SSE 路径表现一致。
6. 第三方 API Key 调用：只凭一个 submit 端点走完全程（首次不带 id → 拿 executionId → 答审批 → 到终态）；只凭旧 `approvalToken` 提交 → 200「该审批已提交，请勿重复审批」且状态不变。
7. 子执行被取消：父流到点退回「给出当前待办」，不无声断流。
8. 连点提交与多标签页：只生效一次。
9. 重开一轮：`{"restart":true}` 后未答审批失效，全量重跑，记忆按新轮累积。
10. 旧端点已不存在：四个旧路径请求均 404，`POST /workflow-executions/submit` 不带 id 与带 id 均能跑通。
11. 非法入参拦住：1.3 校验矩阵的 7 行组合逐个发请求，均返 400 且文案指名了哪个键。
