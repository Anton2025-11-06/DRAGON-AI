# -*- coding: utf-8 -*-
"""asyncio 工作流执行引擎（参照 MaxKB WorkflowManage 调度策略重写）。

移植自 MaxKB 的核心调度算法：
- run_chain_manage 递归调度  →  _schedule 递归 async 任务
- get_next_node_list 分支路由 →  _next_nodes（branch 端口匹配）
- dependent_node_been_executed AND 汇聚 →  _barrier_ready（入边计数 + 活跃端口匹配）
- 多出边按 y 坐标并行       →  asyncio.gather
- 节点异常分支（exception） →  result.branch_id="exception" 走 branch:exception 端口

新增能力（MaxKB 无 / 前端契约要求）：
- 复合节点子图执行（run_subgraph：LOOP/ITERATION/PARALLEL 的循环体/分支体）
- 审批暂停（APPROVAL 节点）：节点边界落快照 + run() 以 PAUSED 收尾，恢复由
  「指定 executionId 再提交」接口驱动（设计冻结 docs/workflow-approval-memory.md）
- 两种提交模式：RETRY 全量重跑 / CONTINUE 命中已完成节点则 skip（发 skip 事件不重跑）

已废弃（勿再引入）：外部接口触发暂停、resume 断点续跑、_pending_nodes、breakpoints。
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
from typing import Any, Optional

from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.events import EventBus, WorkflowEvent
from service.service_workflow.workflow_engine.graph import (
    OUTPUT_HANDLE, WorkflowGraph, is_branch_handle, branch_id_of,
)
from service.service_workflow.workflow_engine.nodes import NODE_REGISTRY
from service.service_workflow.workflow_engine.nodes.base import (
    APPROVAL_SCOPE_ALL, EMIT_OUTPUT_KEY, AwaitingApproval, NodeExecutionError,
    NodeResult,
)
from common.common_log.log_init import log
from common.common_httpx.httpx import httpx_pool

MAX_PARALLEL_BRANCHES = 50  # 单执行并行分支上限(防画布错配导致的任务爆炸)
NODE_TIMEOUT_DEFAULT = 600  # 单节点执行超时(秒):防外部/工具节点永久挂起(MaxKB 无此保护)

# BUG15：当前任务所属的并行分支 id。asyncio.Task 创建时会复制当时的 contextvars，
# 所以在分支协程里派生的所有子孙节点任务都自动带上分支标记，兄弟分支看不到 ——
# 这是「按分支收敛等待 / 按分支取消」能成立的前提。
_CURRENT_BRANCH: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "workflow_parallel_branch", default=None)

# ---------- 执行/节点状态常量(与 tb_workflow_execution.status 及前端展示一致,禁止写裸字符串) ----------
STATUS_PENDING = "PENDING"  # 初始状态(执行未调度 / 节点未开始)
STATUS_RUNNING = "RUNNING"  # 执行中 / 节点运行中
STATUS_PAUSED = "PAUSED"  # 暂停(仅执行记录与运行时使用)
STATUS_COMPLETED = "COMPLETED"  # 成功完成
STATUS_CANCELLED = "CANCELLED"  # 取消(仅执行记录与运行时使用)
STATUS_TIMEOUT = "TIMEOUT"  # 超时(并行分支等待时限到点,节点被停止等待)
STATUS_FAILED = "FAILED"  # 失败
# 节点级专属：等待人工审批（不阻塞协程，run() 收尾时据此把工作流置 PAUSED）
STATUS_AWAITING = "AWAITING"

# 提交模式（tb_workflow_execution.submit_mode）：本轮提交是重新执行还是暂停后恢复
SUBMIT_MODE_RETRY = "RETRY"
SUBMIT_MODE_CONTINUE = "CONTINUE"
# 可再次提交的结束态。PAUSED 不在其中——它是「等待审批」的可提交等待态，
# 只允许 CONTINUE 模式；RUNNING/PENDING 才是禁止提交的「上次任务未结束」。
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)


def compute_graph_hash(graph_raw: Any) -> str:
    """图拓扑指纹（再提交前的漂移校验）：节点 id 集合 + 边集合的 sha1 前 16 位。

    只看拓扑不看配置与坐标：改个提示词、挪一下节点不该拒掉恢复提交，但删节点、
    改连线会让「按上轮状态续跑」失去意义（skip 出来的链路和新连线对不上）。
    DEBUG 预览运行的画布是草稿，随时会变，同样靠这个值兜住。
    """
    raw = graph_raw if isinstance(graph_raw, dict) else {}
    nodes = sorted({str(n.get("id")) for n in (raw.get("nodes") or [])
                    if isinstance(n, dict) and n.get("id")})
    edges = sorted(
        f"{e.get('source')}->{e.get('target')}#{e.get('sourceHandle') or OUTPUT_HANDLE}"
        for e in (raw.get("edges") or []) if isinstance(e, dict))
    digest = json.dumps({"n": nodes, "e": edges}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(digest.encode("utf-8")).hexdigest()[:16]


class WorkflowCancelled(Exception):
    pass


class NodeState:
    """节点执行状态（对齐前端 NodeExecutionState）。

    除展示字段外还承载「跨轮恢复所需的权威数据」：branch（上轮走的出边端口）、
    llmMessages（大模型记忆）、review*（审批结论与编辑 diff）—— 全部随
    tb_workflow_execution.node_states JSON 落库，恢复时靠它重建执行上下文。
    """

    __slots__ = ("order", "status", "input", "output", "error", "duration",
                 "started_at", "label", "nodeType", "branch", "llmMessages",
                 "review", "reviewBy", "reviewOpinion", "reviewDiff", "skip")

    def __init__(self):
        self.order: int = 0
        self.status: str = STATUS_PENDING
        self.input: Any = None
        self.output: Any = None
        self.error: Optional[str] = None
        self.duration: int = 0
        self.started_at: float = 0.0
        # 节点自定义名称与类型（快照，供执行详情展示，避免前端只能看到 nodeId）
        self.label: Optional[str] = None
        self.nodeType: Optional[str] = None
        # 上轮实际走的出边端口（"exception"/分支 id/None=默认 output）。
        # 恢复调度要靠它决定「跳过之后往哪个后继走」，以及汇合闸门的可达性判断；
        # 丢了它，IF_ELSE/问题分类器/异常分支在恢复时会走错分支。
        self.branch: Optional[str] = None
        # 大模型记忆（[{role, content, round, ts}]）：不随本轮 output 覆写而丢失
        self.llmMessages: list = []
        # 审批节点结论与审计
        self.review: Optional[bool] = None
        self.reviewBy: Optional[str] = None
        self.reviewOpinion: Optional[str] = None
        self.reviewDiff: Optional[list] = None
        # 本轮是否为「跳过已执行节点」（CONTINUE 模式），供明细表与前端展示
        self.skip: bool = False

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "duration": self.duration,
            "label": self.label,
            "nodeType": self.nodeType,
            "branch": self.branch,
            "llmMessages": self.llmMessages,
            "review": self.review,
            "reviewBy": self.reviewBy,
            "reviewOpinion": self.reviewOpinion,
            "reviewDiff": self.reviewDiff,
            "skip": self.skip,
        }


class WorkflowRuntime:
    """单次执行运行时（角色对等 MaxKB WorkflowManage）。

    生命周期（需求 4：全部走 execute_async，由 arq worker 任务驱动）：
        runtime = WorkflowRuntime(graph, execution_id, inputs, ...)
        await runtime.run()
        # 事件：节点事件(含 node.delta)经 EventBus pub hook 实时 PUBLISH
        # 到 Redis 频道（Pub/Sub），无进程内注册表与本地事件缓存
    """

    def __init__(self, graph: WorkflowGraph, execution_id: str,
                 inputs: Optional[dict] = None,
                 model_provider=None,
                 http_client=None,
                 event_bus: EventBus = None,
                 node_persist_hook=None,
                 state_persist_hook=None,
                 status_check_hook=None,
                 node_timeout: int = NODE_TIMEOUT_DEFAULT,
                 trigger_type: str = "DEBUG",
                 user_id: int = 0,
                 workflow_id: int = 0,
                 workflow_version: int = 0,
                 submit_mode: str = SUBMIT_MODE_RETRY,
                 approval_decisions: Optional[dict] = None,
                 round_no: int = 1):
        self.graph = graph
        self.execution_id = execution_id
        self.trigger_type = trigger_type
        self.user_id = user_id
        self.workflow_id = workflow_id
        self.workflow_version = workflow_version
        self.ctx = ExecutionContext(graph, inputs)
        self.bus = event_bus
        self.model_provider = model_provider
        self.http_client = http_client
        self.node_timeout = node_timeout

        # 控制状态:取消由 DB 状态驱动（status_check_hook 每节点前查库）；
        # 暂停只由审批节点触发（不再有外部暂停接口），见 awaiting_nodes。
        self.finished = False
        self.status = STATUS_PENDING

        # 本轮提交模式：RETRY 全部重跑 / CONTINUE 已完成节点走 skip
        self.submit_mode = submit_mode or SUBMIT_MODE_RETRY
        # 对话轮次（execution_id 即会话 id，每次 RETRY 提交 +1；CONTINUE 是同一轮
        # 的中断续跑，不另起一轮）。记忆条目靠它排序跨轮的历史。
        self.round = max(1, int(round_no or 1))
        # 本轮携带的审批结论 {node_id: {approved, opinion, edits, reviewBy}}
        # —— 只放本次提交的新结论，历史结论不回灌（防重复审批/重复回写）
        self.approval_decisions: dict[str, dict] = dict(approval_decisions or {})
        # 等待审批的节点 {node_id: 审批上下文}，非空 → run() 收尾置 PAUSED
        self.awaiting_nodes: dict[str, dict] = {}
        # 子图重跑时的记忆暂存 {node_id: llmMessages}：_reset_subgraph_nodes 必须
        # pop 掉 node_states（否则 CONTINUE 的 skip 判据会让循环体只跑一轮），
        # 但大模型记忆不能被一起清掉，先暂存、节点重新执行时回位。
        self._memory_archive: dict[str, list] = {}
        # 审批暂停范围 ALL：True 时砍掉在跑任务、不再调度新节点
        self.halt_all = False

        # 执行状态
        self.node_states: dict[str, NodeState] = {}
        self._order_counter = 0
        self._completed_with_branch: dict[str, Optional[str]] = {}  # node_id -> 活跃出边端口
        # 本轮真正执行过（含 skip）的节点：LOOP 回边守卫与输出收集的唯一依据。
        # 不能用 node_states 的终态判断——恢复时里面全是上一轮的陈旧状态。
        self._executed_this_round: set[str] = set()
        self._running_tasks: set[asyncio.Task] = set()
        # BUG15：并行分支短路所需的登记
        # _branch_tasks/_branch_wrappers: 分支派生的节点任务与分支协程，用于精确取消
        # _cancelled_nodes: 被「任一完成」短路掉的分支节点（视为不可达，不再执行）
        # _blocked_targets: 因 AND 闸门未就绪而被推迟的汇合节点，闸门变化后重放
        # _scheduled_nodes: 已派生过调度任务的节点（防汇合重放导致重复执行）
        self._branch_tasks: dict[str, dict[asyncio.Task, str]] = {}
        self._branch_wrappers: dict[str, asyncio.Task] = {}
        self._branch_entries: dict[str, list[str]] = {}
        self._cancelled_nodes: set[str] = set()
        self._blocked_targets: dict[str, Any] = {}
        self._scheduled_nodes: set[str] = set()
        self.outputs: dict = {}
        self.error: Optional[str] = None
        # 跨轮累计基准（CONTINUE 由 service 植入上轮值，RETRY/新执行为 0）
        self.duration_base_ms: int = 0
        self.duration_ms: int = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_call_count = 0
        # 审批「不同意」时按配置兜底成工作流回答的文案（下游 END 被砍时启用）
        self.reject_replies: dict[str, str] = {}

        # 持久化钩子(service 层注入:节点明细落库 / 执行状态落库 / DB 状态查询)
        self.node_persist_hook = node_persist_hook
        self.state_persist_hook = state_persist_hook
        self.status_check_hook = status_check_hook

    # ==================== 事件 ====================

    async def emit(self, event_type: str, **payload) -> None:
        await self.bus.emit(event_type, **payload)

    def bump_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.input_tokens += int(prompt_tokens or 0)
        self.output_tokens += int(completion_tokens or 0)
        self.llm_call_count += 1

    # ==================== 主流程 ====================

    async def run(self) -> dict:
        """执行整个工作流。返回 outputs(或抛出异常由调用方落 FAILED)。
    
        状态控制语义:
        - 取消:API 把执行记录改为 CANCELLED,引擎在每个节点调度入口查库
          (_checkpoint) → 抛 WorkflowCancelled 走取消分支收尾;
        - 暂停:唯一来源是审批节点。本轮拿不到结论的审批节点记进 awaiting_nodes
          并终止本分支(暂停范围 ALL 时连在跑分支一起砍),其余分支照常跑完;
          run() 收尾发现 awaiting_nodes 非空 → 状态 PAUSED + 落快照 + workflow.paused;
        - 恢复与重跑都走「指定 executionId 再提交」接口,两种模式都从 START 遍历:
          RETRY 全部重跑;CONTINUE 命中上轮 COMPLETED 的节点走 skip。
        """
        start = time.monotonic()
        self.status = STATUS_RUNNING
        try:
            # 事件语义（冻结文档第 4 节）：CONTINUE 首帧发 workflow.resumed，与
            # workflow.started 二选一——两条都发会让订阅方把「恢复」当成新一轮执行重置。
            if self.submit_mode == SUBMIT_MODE_CONTINUE:
                await self.emit("workflow.resumed", inputs=self.ctx.inputs)
            else:
                await self.emit("workflow.started", inputs=self.ctx.inputs)
            # 两种提交模式都从 START 走一遍:CONTINUE 靠 _schedule 里的 skip 判据
            # 续上路由(RETRY 全部重跑),不再依赖已废弃的 _pending_nodes。
            start_node = self.graph.find_start_node()
            if start_node is None:
                raise NodeExecutionError("工作流缺少 START 节点")
            await self._schedule(start_node.id)
            await self._wait_running()

            # 终态检查点:末节点执行期间到达的取消,在收尾前再查一次 DB 状态,
            # 避免「全部节点已跑完但 DB 已不是 RUNNING」时仍按成功落库。
            await self._checkpoint()
            # 审批等待收尾:存在未决策的审批节点 → 本轮到此为止,状态 PAUSED。
            # 不能发 workflow.completed——对话页会把「还没跑完的半条流」当成回答定稿,
            # 订阅方也无从得知接下来该提交审批结论。
            if self.awaiting_nodes:
                self.status = STATUS_PAUSED
                self.duration_ms = self.duration_base_ms + int((time.monotonic() - start) * 1000)
                self.outputs = {}
                await self._persist_state(paused=True)
                nid = next(iter(self.awaiting_nodes))
                await self.emit("workflow.paused", nodeId=nid,
                                awaitingNodeIds=list(self.awaiting_nodes),
                                approvalContext=self.awaiting_nodes[nid],
                                variables=self.ctx.global_vars)
                return self.outputs
            # 终态兜底:节点失败会把工作流状态置 FAILED,而该异常在并行多目标下
            # 会被 _route_next 的 gather 兜住不上抛。此处若仍只在 RUNNING 分支
            # 收尾,就出现「节点全跑完但既不落库也不发终态事件」——SSE 订阅端靠
            # DB 状态兜底会一直读到 RUNNING,对话页永远停在「执行中」。
            # 按当前状态补走对应收尾(异常由下面的 except 统一 emit 终态事件)。
            if self.status == STATUS_FAILED:
                raise NodeExecutionError(self.error or "工作流执行失败")
            if self.status == STATUS_CANCELLED:
                raise WorkflowCancelled()
            # 全图执行完毕 → 收集 END 节点输出(无 END 则为空)
            self.status = STATUS_COMPLETED
            self.duration_ms = self.duration_base_ms + int((time.monotonic() - start) * 1000)
            self.outputs = self._collect_outputs()
            await self._persist_state()
            await self.emit("workflow.completed", outputs=self.outputs, duration=self.duration_ms)
            return self.outputs
        except WorkflowCancelled:
            self.status = STATUS_CANCELLED
            self.duration_ms = self.duration_base_ms + int((time.monotonic() - start) * 1000)
            # 取消仍在执行的节点任务:防任务泄漏与取消后半途写库
            for t in list(self._running_tasks):
                t.cancel()
            await self._persist_state()
            await self.emit("workflow.cancelled")
            return {}
        except Exception as e:  # noqa: BLE001
            self.status = STATUS_FAILED
            self.error = str(e)
            self.duration_ms = self.duration_base_ms + int((time.monotonic() - start) * 1000)
            await self._persist_state()
            await self.emit("workflow.failed", error=str(e))
            raise
        finally:
            self.finished = True
            # 无本地缓冲/连接可释放:事件已实时 PUBLISH,无需再 close

    async def _wait_running(self, exclude: Optional[set] = None) -> None:
        me = asyncio.current_task()
        excl = exclude or set()
        while True:
            # 排除当前任务自身及其祖先复合节点任务（复合节点以 task 形式被调度时自身在
            # _running_tasks 中，若一并等待会自死锁）
            pending = [t for t in self._running_tasks
                       if t is not me and t not in excl]
            if not pending:
                return
            if self.halt_all:
                # 审批要求「整条工作流全部暂停」:砍掉仍在跑的任务,不再往下调度。
                await self._cancel_running(keep={me} | excl)
                return
            done, _ = await asyncio.wait(pending, timeout=0.5,
                                         return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                self._running_tasks.discard(t)
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is not None and not isinstance(exc, (WorkflowCancelled,)):
                    # 分支任务里的异常已在其内部处理为 node.failed；此处兜底上抛
                    raise exc
            # 注:取消不在这里打断等待——相关任务会在各自的调度入口检查点
            # (_checkpoint)自行终止,这里只需等所有任务自然结束。

    async def _cancel_running(self, keep: set) -> None:
        """砍掉仍在跑的节点任务（审批「整条流暂停」），并把停在 RUNNING 的节点收敛成取消态。

        与并行短路砍分支同一口径:不发终态事件的节点会在页面永远停在蓝色「执行中」;
        收敛为 CANCELLED 后,下轮恢复按「非 COMPLETED → 重跑」的 skip 判据重新执行。
        """
        targets = [t for t in self._running_tasks if t not in keep and not t.done()]
        self._running_tasks.difference_update(targets)
        for t in targets:
            t.cancel()
        if targets:
            await asyncio.wait(targets, timeout=5)
        for nid, state in list(self.node_states.items()):
            if state.status != STATUS_RUNNING or nid in self.awaiting_nodes:
                continue
            state.status = STATUS_CANCELLED
            state.error = "审批暂停（暂停范围：整条工作流），本节点将在恢复后重跑"
            state.duration = int((time.monotonic() - state.started_at) * 1000)
            self._cancelled_nodes.add(nid)
            target = self.graph.get_node(nid)
            if target is not None:
                await self._persist_node(target, state)
            await self.emit("node.cancelled", nodeId=nid, duration=state.duration,
                            reason=state.error)

    async def _persist_node(self, node, state) -> None:
        """节点明细落库（await 串行，不再 create_task 火忘）。

        火忘的迟到写会在「同一 executionId 再提交」时把上一轮数据盖到新一轮上，
        而再提交的唯一互斥手段是 DB 状态，所以落库必须在引擎推进前完成。
        失败只告警：明细表是展示/排障数据，不能因为它丢掉真实执行。
        """
        if not self.node_persist_hook:
            return
        try:
            await self.node_persist_hook(self, node, state)
        except Exception as e:  # noqa: BLE001
            log.warning("node persist failed exec={} node={}: {}", self.execution_id, node.id, e)

    # ==================== 调度（对照 MaxKB run_chain_manage/get_next_node_list） ====================

    async def _schedule(self, node_id: str) -> None:
        """调度一个节点:执行(或 skip) → 路由出边 → 并行调度后继。"""
        node = self.graph.get_node(node_id)
        if node is None:
            return
        if node_id in self._executed_this_round:
            return  # 本轮已执行(LOOP 回边场景)
        if node_id in self._cancelled_nodes:
            # BUG15：并行「任一完成」/审批不同意已短路掉的节点，不再执行
            return
        if node_id in self._scheduled_nodes:
            # BUG15：同一节点的调度任务已派生过（并行汇合节点重放等场景），防重复执行
            return
        self._scheduled_nodes.add(node_id)

        # 状态检查点:每个节点执行前查一次 DB 状态。取消是唯一的即时外部干预
        # （暂停改由审批节点在节点边界触发，不依赖此检查点）。
        await self._checkpoint()
        if self.halt_all:
            # 审批「整条工作流暂停」已生效:本节点本轮不跑。它不会进 node_states，
            # 下轮恢复时 skip 判据不成立 → 自然重跑，无需额外补登记。
            return

        # CONTINUE：上轮已 COMPLETED 的节点不重跑，只补发 skip 事件并沿上轮出边端口续路由
        if self.submit_mode == SUBMIT_MODE_CONTINUE and self._should_skip(node):
            result = await self._skip_node(node)
        else:
            result = await self._execute_node(node)

        # 分支失败且无异常分支 / 审批挂起 / 不同意已就地收口 → 本分支终止
        if result is None:
            return

        # BUG15：并行节点「任一完成」短路取消未命中分支后，重新放行当时被 AND 闸门
        # 挡下的汇合节点（放在本节点输出已写入之后，下游引用才安全）。
        if self._blocked_targets:
            await self._release_blocked()

        await self._route_next(node, result)

    def _should_skip(self, node) -> bool:
        """CONTINUE 模式下的跳过判据：上轮 COMPLETED 才跳。

        FAILED/CANCELLED/TIMEOUT/RUNNING（整条流暂停时被砍的）一律重跑；
        审批节点特例：AWAITING 必然不跳（走本轮结论覆写）；已 COMPLETED（往轮
        已决策）按普通节点跳过——本轮没带它的新结论，重跑只会重复挂一次审批。
        """
        state = self.node_states.get(node.id)
        return state is not None and state.status == STATUS_COMPLETED

    async def _skip_node(self, node) -> NodeResult:
        """跳过上轮已完成的节点：不跑业务逻辑，只补发带 skip 标记的成对事件。

        为什么要发事：订阅方（对话页/调试面板）要靠事件流还原整条链路，
        不发的话这些节点在页面上根本不会出现。output 不重发（真实数据以详情接口
        的 nodeStates 为准），耗时计 0，并补写一行明细（否则提交时物理删过的
        明细表只剩本轮真跑的节点）。
        """
        state = self.node_states[node.id]
        state.skip = True
        self._executed_this_round.add(node.id)
        self._completed_with_branch[node.id] = state.branch
        if node.id not in self.ctx.executed:
            self.ctx.executed.append(node.id)
        output = state.output if isinstance(state.output, dict) else {}
        await self.emit("node.started", nodeId=node.id, nodeType=node.type,
                        input=state.input, skip=True)
        await self.emit("node.completed", nodeId=node.id, duration=0, skip=True)
        await self._persist_node(node, state)
        return NodeResult(output=output, branch_id=state.branch)

    async def _execute_node(self, node) -> Optional[NodeResult]:
        """执行单个节点（含事件、计时、状态记录、异常处理）。"""
        state = self.node_states.get(node.id) or NodeState()
        if not state.llmMessages and node.id in self._memory_archive:
            # 刚被子图重置抹掉的节点：把它的记忆接回来（子图内每轮覆盖，不累加）
            state.llmMessages = self._memory_archive.pop(node.id)
        self._executed_this_round.add(node.id)
        self._order_counter += 1
        state.order = self._order_counter
        state.status = STATUS_RUNNING
        state.error = None
        state.skip = False
        state.started_at = time.monotonic()
        # 快照节点自定义名称/类型（执行时为准，不受后续画布改名影响）
        state.label = node.label
        state.nodeType = node.type
        self.node_states[node.id] = state

        input_view = self._node_input_view(node)
        state.input = input_view

        started = time.monotonic()
        await self.emit("node.started", nodeId=node.id, nodeType=node.type, input=input_view)
        await self._persist_node(node, state)

        try:
            executor_cls = NODE_REGISTRY.get(node.type)
            if executor_cls is None:
                raise NodeExecutionError(f"未知节点类型: {node.type}")
            executor = executor_cls(node, self)
            # 单节点超时保护（MaxKB 缺失）：节点 data.timeout 可覆盖，逾时按节点失败处理，
            # 避免外部/工具/模型节点异常挂起拖垮整条工作流。
            timeout = int(node.data.get("timeout") or self.node_timeout or NODE_TIMEOUT_DEFAULT)
            try:
                result = await asyncio.wait_for(executor.execute(self.ctx), timeout=timeout)
            except asyncio.TimeoutError:
                raise NodeExecutionError(
                    f"节点「{node.label}」执行超时（>{timeout}s）")
        except WorkflowCancelled:
            raise
        except AwaitingApproval as a:
            # 审批挂起：不是失败也不是取消。节点停在 AWAITING（非 COMPLETED → 下轮必重跑），
            # 本分支终止且不路由下游，返回 None 让 _schedule 安静收场。
            return await self._enter_awaiting(node, state, a)
        except Exception as e:  # noqa: BLE001
            state.status = STATUS_FAILED
            state.error = str(e)
            state.output = None

            await self._persist_node(node, state)

            state.duration = int((time.monotonic() - started) * 1000)
            await self.emit("node.failed", nodeId=node.id, error=str(e))

            # 异常分支：branch:exception 出边存在则继续，否则终止工作流
            if self.graph.get_out_edges(node.id, "exception"):
                self._completed_with_branch[node.id] = "exception"
                state.branch = "exception"
                self.ctx.set_node_output(node.id, {"exception_message": str(e)})
                return NodeResult(output={"exception_message": str(e)}, branch_id="exception")
            self.error = f"节点「{node.label}」执行失败: {e}"
            self.status = STATUS_FAILED
            raise NodeExecutionError(self.error)

        # 成功
        state.status = STATUS_COMPLETED
        state.branch = result.branch_id

        state.output = result.output
        self.ctx.set_node_output(node.id, result.output)
        self.ctx.executed.append(node.id)
        self._completed_with_branch[node.id] = result.branch_id  # None → output 端口

        await self._persist_node(node, state)

        state.duration = int((time.monotonic() - started) * 1000)
        # 输出开关打开: 广播输出
        if node.data.get(EMIT_OUTPUT_KEY, True) is True:
            if node.get_config("streaming", False) is False:
                await self.emit("node.completed", nodeId=node.id, output=state.output, duration=state.duration)
            else:
                # 如果开启流式输出，则已经emit了node.delta。 此处无需再次发送流式的完整输出
                await self.emit("node.completed", nodeId=node.id, duration=state.duration)
        else:
            # 输出开关关闭: 只广播节点完成事件,不广播输出
            await self.emit("node.completed", nodeId=node.id, duration=state.duration)

        return result

    def _node_input_view(self, node) -> dict:
        """节点输入视图（调试展示用）：该节点入边来源节点的输出摘要。

        BUG16：并行多路汇聚时，同类型节点的默认名称是一样的（并行开两个大模型，
        两个节点都叫「大模型」），旧实现直接拿名称做 key → 后一路覆盖前一路，
        下游输入里看起来只剩一路输出。现在：
        - 同一批入边里重名的来源，统一用「名称#节点id」，每一路都看得到；
        - 同一来源的多条入边（如 IF_ELSE 两个出口连同一节点）只展示一次；
        - 未真正跑出结果的来源（并行「任一完成」被短路取消的分支）不占输入位，
          避免拿一路 null 冒充分支结果。

        并行分支入口特殊处理：PARALLEL 要等所有分支汇合才 COMPLETED，分支里的节点
        （如并行节点后直接接的大模型）执行时它还是 RUNNING，只按 COMPLETED 取会整块空
        → 退而取执行器提前写入 ctx 的透传快照。
        """
        sources: list = []
        seen: set[str] = set()
        for e in self.graph.get_in_edges(node.id) or []:
            if e.source in seen:
                continue
            seen.add(e.source)
            src = self.graph.get_node(e.source)
            state = self.node_states.get(e.source)
            if src is None or state is None:
                continue
            if state.status in (STATUS_COMPLETED, STATUS_FAILED):
                sources.append((src, state.output))
                continue
            if state.status == STATUS_RUNNING:
                interim = self.ctx.get_node_output(src.id)
                if interim:
                    sources.append((src, interim))
        names = [src.label or src.id for src, _ in sources]
        duplicated = {name for name in names if names.count(name) > 1}
        view: dict = {}
        for src, output in sources:
            key = src.label or src.id
            view[f"{key}#{src.id}" if key in duplicated else key] = output
        return view

    async def _route_next(self, node, result: NodeResult) -> None:
        """路由出边（对照 MaxKB get_next_node_list）。

        1. result.branch_id 非空 → 匹配 branch:{id} 端口的出边（无匹配视为分支终止）
        2. 否则匹配默认 output 端口的出边
        3. 目标节点多入边（AND 汇聚）→ 所有入边就绪才调度
        4. 多目标并行（按 y 坐标排序，asyncio.gather）
        """
        next_pairs = self.graph.get_next_nodes(node.id, result.branch_id)
        ready = []
        for target, edge in next_pairs:
            if target.id in self._scheduled_nodes or target.id in self._cancelled_nodes:
                continue  # 已在执行/已短路，不重复派生
            if self._barrier_ready(target.id):
                ready.append(target)
            else:
                # BUG15：记下来，闸门条件变化后（如并行未命中分支被取消）重放
                self._blocked_targets[target.id] = target
        if not ready:
            return
        ready.sort(key=lambda n: n.y)
        if len(ready) == 1:
            self._spawn(ready[0].id, owned_by_branch=not self._is_convergence(ready[0].id))
        else:
            if len(ready) > MAX_PARALLEL_BRANCHES:
                raise NodeExecutionError(f"节点「{node.label}」并行分支过多: {len(ready)}")
            inner = [self._spawn(t.id, owned_by_branch=not self._is_convergence(t.id))
                     for t in ready]
            # gather 兜住多目标：任一目标失败不影响其余目标继续跑完；但失败不能吞，
            # 全部跑完后仍需上抛第一个异常（见 _await_targets）
            wrapper = asyncio.ensure_future(self._await_targets(inner))
            self._running_tasks.add(wrapper)
            wrapper.add_done_callback(self._running_tasks.discard)

    async def _await_targets(self, tasks: list) -> None:
        """等全部并行目标自然结束，再上抛第一个非取消异常。

        与 run_branches 里「分支内节点失败不可静默吞掉（否则并行节点会假成功）」
        同一口径：吞掉异常会让 run() 收尾误判状态、丢掉 workflow.failed 事件。
        WorkflowCancelled 是并行短路砍分支的正常信号，不算失败，跳过。
        """
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception) and not isinstance(res, WorkflowCancelled):
                raise res

    def _spawn(self, node_id: str, owned_by_branch: bool = True) -> asyncio.Task:
        """派生一个节点调度任务，并在并行分支上下文中登记归属。

        owned_by_branch=False：目标是多入边汇合节点，它不属于任何单个并行分支，
        必须脱离分支上下文创建 —— 否则所在分支被「任一完成」短路取消时，会被连累
        一起砍掉（汇合节点正等着所有分支，取消它等于取消整条下游）。
        """
        branch = _CURRENT_BRANCH.get() if owned_by_branch else None
        if branch is None:
            token = _CURRENT_BRANCH.set(None)
            try:
                task = asyncio.create_task(self._schedule(node_id))
            finally:
                _CURRENT_BRANCH.reset(token)
        else:
            task = asyncio.create_task(self._schedule(node_id))
            bucket = self._branch_tasks.setdefault(branch, {})
            bucket[task] = node_id
            task.add_done_callback(lambda t, b=bucket: b.pop(t, None))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)
        return task

    def _is_convergence(self, node_id: str) -> bool:
        """多入边汇合节点（AND 汇聚语义）。"""
        return len(self.graph.get_in_edges(node_id) or []) > 1

    def _cancel_branch(self, bid: str) -> tuple[list[asyncio.Task], set[str]]:
        """取消一个未完成的并行分支：分支协程 + 它派生的全部节点任务。

        只 cancel 分支协程不够 —— 子节点是以独立 task 跑在 _running_tasks 里的，
        不一起取消就会出现「并行节点已返回继续，未命中分支还在后台跑完」的悬挂执行。

        返回 (被取消的 task, 被砍掉的节点 id)：task 交调用方 await 落地，节点 id
        用于终态收敛与事件广播（见 _settle_branch_nodes）。
        """
        cancelled: list[asyncio.Task] = []
        nodes: set[str] = set()
        wrapper = self._branch_wrappers.get(bid)
        if wrapper is not None and not wrapper.done():
            wrapper.cancel()
            cancelled.append(wrapper)
        for task, nid in list(self._branch_tasks.get(bid, {}).items()):
            if not task.done():
                task.cancel()
                cancelled.append(task)
            nodes.add(nid)
        nodes.update(self._branch_entries.get(bid, []))
        self._cancelled_nodes.update(nodes)
        return cancelled, nodes

    async def _settle_branch_nodes(self, stranded: dict[str, set[str]], *, timed_out: bool,
                                   barrier, timeout_ms: int = 0) -> None:
        """并行分支被砍后：节点终态收敛 + 广播终态事件。

        旧实现只改内存状态、一个事件都不发 —— 分支里的节点收到过 node.started
        之后再没有终态，画布与节点追踪就一直停在蓝色「执行中」（并行「任一完成」
        + 等待超时最容易看到的观感）。按砍掉的原因分两类：
        - timed_out：等待时限到点仍没跑完 → STATUS_TIMEOUT + node.timeout
          （页面黄色「已超时」）；
        - 非 timed_out：同组其他分支先完成被短路 → STATUS_CANCELLED + node.cancelled
          （页面灰色「已取消」）。
        只处理仍停在 RUNNING 的节点，已完成/已失败的终态不被覆盖。
        """
        status = STATUS_TIMEOUT if timed_out else STATUS_CANCELLED
        event_type = "node.timeout" if timed_out else "node.cancelled"
        reason = (f"并行分支「{barrier.label}」等待超时（>{timeout_ms}ms），已停止等待"
                  if timed_out else
                  f"并行分支「{barrier.label}」其他分支先完成，本分支已取消")
        settled: list[str] = []
        for bid, node_ids in stranded.items():
            for nid in sorted(node_ids):
                state = self.node_states.get(nid)
                if state is None or state.status != STATUS_RUNNING:
                    continue
                state.status = status
                state.duration = int((time.monotonic() - state.started_at) * 1000)
                state.error = reason
                settled.append(nid)
                target = self.graph.get_node(nid)
                if target is not None:
                    # 状态同步落库（await：不能火忘，否则执行收尾后迟到的写会跨轮脏写）
                    await self._persist_node(target, state)
                await self.emit(event_type, nodeId=nid, branchId=bid,
                                duration=state.duration, error=reason)
        if settled:
            log.warning("parallel branch nodes settled exec={} barrier={} timeout={} nodes={}",
                        self.execution_id, barrier.label, timed_out, settled)

    async def _release_blocked(self) -> None:
        """重新放行此前被 AND 闸门挡下的下游节点。

        BUG15：分支取消发生在「先完成分支已经路由过一次」之后 —— 汇合节点当时因
        未命中分支仍在执行而没有放行，取消完分支后没人再触发它，下游就永久漏跑了。
        """
        waiting, self._blocked_targets = self._blocked_targets, {}
        for nid in list(waiting):
            if nid in self._scheduled_nodes or nid in self._cancelled_nodes:
                continue
            if self._barrier_ready(nid):
                self._spawn(nid, owned_by_branch=False)
            else:
                self._blocked_targets[nid] = waiting[nid]

    def _is_reachable(self, node_id: str, visiting: Optional[set] = None) -> bool:
        """节点在「已执行的决策」下是否仍可达（用于汇合门放行不可达分支）。

        可达 = START，或存在一条入边满足：
          - 源已执行且走了该边（活跃），或
          - 源尚未执行但源本身可达（将来会执行）。
        环（LOOP 回边）按保守策略视为可达，避免误阻塞；visiting 防无限递归。
        """
        if visiting is None:
            visiting = set()
        if node_id in visiting:
            return True
        if node_id in self._cancelled_nodes:
            # BUG15：并行「任一完成」已短路掉的分支节点不会再执行 → 视为不可达，
            # 否则下游汇合节点会一直等一个永远不会完成的来源（闸门死锁）
            return False
        visiting.add(node_id)
        try:
            start = self.graph.find_start_node()
            if start is not None and start.id == node_id:
                return True
            for e in self.graph.get_in_edges(node_id):
                s = e.source
                s_state = self.node_states.get(s)
                if s_state is not None and s_state.status in (
                        STATUS_COMPLETED, STATUS_FAILED):
                    # 与 _barrier_ready 一致：源已终结按活跃端口判断（FAILED+exception 视为活跃）
                    active = self._completed_with_branch.get(s)
                    expected = e.source_handle or OUTPUT_HANDLE
                    actual = f"branch:{active}" if active else OUTPUT_HANDLE
                    edge_active = (expected == actual)
                else:
                    edge_active = self._is_reachable(s, visiting)
                if edge_active:
                    return True
            return False
        finally:
            visiting.discard(node_id)

    def _barrier_ready(self, node_id: str) -> bool:
        """AND 汇聚判断（对照 MaxKB dependent_node_been_executed）。

        目标节点的每条入边需满足：
        - 源已执行完成且活跃端口 == 该边 sourceHandle → 该边就绪
        - 源已完成但未走该边 → 该边不激活，跳过
        - 源尚未执行：仍可达（将来执行）→ 等待；不可达（所在分支未激活）→ 跳过
        """
        for e in self.graph.get_in_edges(node_id):
            src_state = self.node_states.get(e.source)
            if src_state is not None and src_state.status in (
                    STATUS_COMPLETED, STATUS_FAILED):
                # 源已终结（完成或失败）：按 _completed_with_branch 记录的活跃端口判断。
                # FAILED + exception 分支（_execute_node 已记录 branch）时下游异常
                # 分支节点必须放行，否则失败降级路径会被 AND 闸门永久卡死，
                # 执行静默 COMPLETED 丢失后续节点。
                expected = e.source_handle or OUTPUT_HANDLE
                active = self._completed_with_branch.get(e.source)
                actual = f"branch:{active}" if active else OUTPUT_HANDLE
                if expected != actual:
                    continue  # 已终结但走的不是这条边 → 边不激活，跳过
                continue  # 已终结且走的就是这条边 → 就绪
            # 源尚未执行：可达则等待，不可达则跳过（如未激活分支上的前置节点）
            if self._is_reachable(e.source):
                return False
            continue
        return True

    def _barrier_ready_except(self, node_id: str, skip_source: str) -> bool:
        """AND 汇聚判断，但忽略来自 skip_source 的那条入边。

        并行分支入口节点的入边之一就是并行节点自己 —— 它此刻正在执行分支，不可能有
        「已完成 + 活跃端口」记录，直接用 _barrier_ready 判断会永远不放行（ANY 又退化
        成等全部），所以只校验外部前置来源是否还在跑。
        """
        for e in self.graph.get_in_edges(node_id) or []:
            if e.source == skip_source:
                continue
            src_state = self.node_states.get(e.source)
            if src_state is not None and src_state.status in (
                    STATUS_COMPLETED, STATUS_FAILED):
                continue  # 外部来源已终结，不会再执行
            if self._is_reachable(e.source):
                return False
        return True

    # ==================== 子图执行（LOOP/ITERATION/PARALLEL 用） ====================

    async def run_subgraph(self, entry_node_id: str, exit_node_id: Optional[str] = None,
                           scope_vars: Optional[dict] = None,
                           wait_all: bool = True) -> dict:
        """执行 entry 起始的子图，遇到 exit_node_id（不执行它）或无出边即止。

        返回子图内所有已完成节点的输出聚合 {node_id: output}。
        scope_vars 压入局部作用域（ITERATION 的 item/index）。
        复合节点（LOOP/ITERATION）每轮都会重复调用本方法，因此进入前重置子图节点
        的完成状态，避免 _schedule 的「已完成则跳过」守卫导致循环体只跑一次。
        """
        if scope_vars:
            self.ctx.push_scope(scope_vars)
        order_before = self._order_counter
        self._reset_subgraph_nodes(entry_node_id, exit_node_id)
        # 快照进入前已存在的任务（含调用方——复合节点的调度 task）。
        # 复合节点以 asyncio.create_task 形式被调度，其 task 就在 _running_tasks 中，
        # 而本方法内部被 asyncio.wait_for 包裹，asyncio.current_task() 拿到的是
        # wait_for 的内部任务而非该祖先 task，若仅凭「非自身」过滤会漏掉祖先 task，
        # 导致内层一直等待正阻塞在 run_subgraph 返回上的祖先 → 自死锁。
        # 故用进入时刻的快照排除所有祖先/pre-existing 任务，仅等待本子图「衍生」的任务。
        preexisting = set(self._running_tasks)
        try:
            await self._checkpoint()
            await self._schedule(entry_node_id)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                pending = [t for t in self._running_tasks if t not in preexisting]
                if not pending:
                    break
                await asyncio.wait(pending, timeout=0.2,
                                   return_when=asyncio.FIRST_COMPLETED)
            result = self._collect_new_outputs(entry_node_id, order_before)
            return result
        finally:
            if scope_vars:
                self.ctx.pop_scope()

    def _collect_new_outputs(self, entry_node_id, order_before: int) -> dict:
        """收集入口可达子图内「本轮（order>order_before）新增执行完成」节点的最新输出。

        entry_node_id 可以是单个入口 id，也可以是入口 id 列表（并行分支按 output
        端口多出口时，一个分支可能对应多个入口节点）。

        与旧实现（executed 集合差）的区别：
        - LOOP/ITERATION body 节点每轮重复执行且 id 相同，集合差会把第二轮及以后的
          输出全部丢弃（loopResult/items 为空）；按 order 过滤只认本轮新执行的
        - PARALLEL 分支并发执行时全局 executed 交错，集合差会把相邻分支的节点
          误收进本分支结果；按入口可达闭包过滤只收本分支子图的节点
        """
        reachable: set[str] = set()
        entries = ([entry_node_id] if isinstance(entry_node_id, str)
                   else list(entry_node_id or []))
        stack = list(entries)
        while stack:
            nid = stack.pop()
            if nid in reachable or nid is None:
                continue
            reachable.add(nid)
            for e in self.graph.get_out_edges(nid):
                stack.append(e.target)
        return {nid: self.ctx.get_node_output(nid)
                for nid, st in self.node_states.items()
                if nid in reachable and st.order > order_before}

    def _reset_subgraph_nodes(self, entry_node_id: str, exit_node_id: Optional[str]) -> None:
        """重置 entry 可达子图（不含 exit）的完成状态，使复合节点每轮能重跑子图。"""
        seen: set[str] = set()
        stack = [entry_node_id]
        while stack:
            nid = stack.pop()
            if nid in seen or nid is None:
                continue
            seen.add(nid)
            old = self.node_states.pop(nid, None)
            if old is not None and old.llmMessages:
                # 状态可以重置，跨轮攒下来的大模型记忆不行
                self._memory_archive[nid] = old.llmMessages
            self._completed_with_branch.pop(nid, None)
            # 子图重跑前释放调度登记，否则 _schedule 的「已派生过」守卫会让
            # LOOP/ITERATION 第二轮起整个循环体直接空跑
            self._scheduled_nodes.discard(nid)
            if nid == exit_node_id:
                continue  # exit 节点不被执行，不继续向下传播
            for e in self.graph.get_out_edges(nid):
                stack.append(e.target)

    async def run_branches(self, node, branch_ids: list[str],
                           wait_strategy: str = "ALL",
                           timeout_ms: int = 0,
                           entries: Optional[dict[str, list[str]]] = None
                           ) -> dict[str, dict]:
        """并行执行节点的多个分支子图，返回 {branch_id: 输出聚合}。

        wait_strategy: ALL 全部等待 / ANY 任一完成 / FIRST 第一个完成（语义同 ANY，按 y 序）
        timeout_ms: 等待时限（毫秒），0 表示不限制
        entries: 分支入口 {branch_id: [node_id]}；缺省按 branch:{id} 端口出边解析

        BUG15 的两处收敛修正：
        1. 每个分支只等自己派生的任务（按 contextvars 分支标记登记），不再用
           「_running_tasks 快照差集」—— 那个写法会把兄弟分支的任务也算进自己的
           等待集合，分支互等，ANY 事实上退化成 ALL；
        2. ANY 的等待时限用节点配置值，不再硬编码 3 秒；且短路时连分支派生的
           子节点 task 一起取消，不是只砍分支协程。

        超时/短路的收口（node.timeout / node.cancelled）：未完成的分支被砍掉后，
        分支里正在跑的节点必须拿到一个终态事件，否则页面永远显示蓝色「执行中」。
        """
        results: dict[str, dict] = {}
        if not branch_ids:
            return results

        def _branch_entries(bid: str) -> list[str]:
            explicit = (entries or {}).get(bid)
            if explicit:
                return [str(nid) for nid in explicit]
            return [t.id for t, _e in self.graph.get_next_nodes(node.id, bid)]

        async def _run_branch(bid: str) -> str:
            # 本 task 的 context 独立：设了分支标记后，由它派生的子孙节点 task 都会
            # 继承并登记到本分支，兄弟分支看不到 → 等待与取消都能按分支精确收敛
            _CURRENT_BRANCH.set(bid)
            entry_ids = _branch_entries(bid)
            if not entry_ids:
                results[bid] = {}
                return bid
            self._branch_entries[bid] = entry_ids
            bucket = self._branch_tasks.setdefault(bid, {})
            order_before = self._order_counter
            for eid in entry_ids:
                if not self._barrier_ready_except(eid, node.id):
                    # 分支入口同时也是外部连线的汇合点：挂进等待名单，等另一个来源
                    # 跑完后由 _release_blocked 放行，不能抢在它的前置节点之前执行
                    self._blocked_targets[eid] = self.graph.get_node(eid)
                    continue
                await self._schedule(eid)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                pending = [t for t in bucket if not t.done()]
                if not pending:
                    break
                await asyncio.wait(pending, timeout=0.2,
                                   return_when=asyncio.FIRST_COMPLETED)
            # 分支内节点失败不可静默吞掉（否则并行节点会「假成功」）
            for task in list(bucket):
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not isinstance(exc, WorkflowCancelled):
                    raise exc
            results[bid] = self._collect_new_outputs(entry_ids, order_before)
            return bid

        tasks: dict[str, asyncio.Task] = {}
        for bid in branch_ids:
            task = asyncio.create_task(_run_branch(bid))
            tasks[bid] = task
            self._branch_wrappers[bid] = task
        # 等待是否因时限到点而结束：True → 未完成的分支算「超时」(node.timeout)，
        # False → 算「被其他分支先完成短路取消」(node.cancelled)，页面配色文案不同
        wait_expired = False
        stranded: dict[str, set[str]] = {}  # branch_id -> 被砍掉的节点 id
        try:
            if wait_strategy in ("ANY", "FIRST"):
                # 只等到「第一个分支完成」为止，其余分支下面统一取消
                done, _pending = await asyncio.wait(
                    list(tasks.values()), return_when=asyncio.FIRST_COMPLETED,
                    timeout=(timeout_ms / 1000) if timeout_ms else None)
                if not done and timeout_ms:
                    # 到时仍无分支完成：不继续等待，带已完成（空）结果往下走
                    wait_expired = True
                    log.warning("parallel branch wait timeout exec={} node={} ms={}",
                                self.execution_id, node.label, timeout_ms)
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        raise exc
            elif timeout_ms:
                done, _pending = await asyncio.wait(
                    list(tasks.values()), timeout=timeout_ms / 1000)
                if len(done) < len(tasks):
                    wait_expired = True
                    log.warning("parallel branches not all finished in {}ms exec={}",
                                timeout_ms, self.execution_id)
            else:
                await asyncio.wait(list(tasks.values()))
        finally:
            # 短路：取消尚未完成的分支（分支协程 + 其派生的全部节点任务）。
            # 收口放 finally 而不是 try 末尾：分支协程抛异常时同样要让被砍的节点
            # 拿到终态，否则那些节点一直停在 RUNNING（页面蓝色「执行中」不消失）
            killed: list[asyncio.Task] = []
            for bid, task in tasks.items():
                if not task.done():
                    branch_killed, nodes = self._cancel_branch(bid)
                    killed.extend(branch_killed)
                    stranded[bid] = nodes
                self._branch_wrappers.pop(bid, None)
            if killed:
                await asyncio.wait(killed, timeout=5)
            if stranded:
                await self._settle_branch_nodes(stranded, timed_out=wait_expired,
                                                barrier=node, timeout_ms=timeout_ms)
        return results

    # ==================== 状态检查(需求 5:DB 状态驱动控制) ====================

    def _check_cancelled(self) -> None:
        """复合节点（LOOP/ITERATION 等）轮询内的取消检查：
        任意节点调度入口或 run() 收尾已把 status 置为 CANCELLED 时立即终止。"""
        if self.status == STATUS_CANCELLED:
            raise WorkflowCancelled()

    async def _checkpoint(self) -> None:
        """节点状态检查点:每个节点执行前调用一次(经注入的 hook 查 DB 状态)。

        - DB 状态为 RUNNING(或 hook 缺失/查询失败)→ 放行,继续执行;
        - DB 状态为 CANCELLED → 抛 WorkflowCancelled,run() 走取消分支统一收尾。

        只认 CANCELLED：暂停不再由外部接口触发，唯一来源是审批节点在节点边界的
        挂起（见 _enter_awaiting）。DB 里残留的 PAUSED 只是「上一轮停在审批等待」的
        结果标记，提交接口已在校验通过后把它置回 RUNNING，这里再按 PAUSED 自我暂停
        会让恢复任务原地空转。
        """
        status = STATUS_RUNNING
        if self.status_check_hook is not None:
            try:
                status = (await self.status_check_hook(self.execution_id)) or STATUS_RUNNING
            except Exception as e:  # noqa: BLE001
                # DB 瞬时不可用按 RUNNING 放行(宁可慢执行也不误杀任务)
                log.warning("status check hook failed exec={}: {}", self.execution_id, e)
                status = STATUS_RUNNING
        if status == STATUS_CANCELLED:
            raise WorkflowCancelled()

    # ==================== 审批：暂停落点与不同意收口 ====================

    async def _enter_awaiting(self, node, state: NodeState,
                              signal: AwaitingApproval) -> None:
        """审批节点挂起：节点停在 AWAITING，本分支终止且不路由下游，返回 None。

        AWAITING 不是终态，所以 CONTINUE 恢复时 skip 判据不成立 → 该节点必然重跑，
        本轮带进来的审批结论在审批执行器里覆写它的输出。
        暂停范围为 ALL 时置 halt_all：其余分支不再调度，在跑的由 _wait_running 砍掉。
        """
        state.status = STATUS_AWAITING
        state.output = None
        state.error = None
        state.duration = int((time.monotonic() - state.started_at) * 1000)
        self.awaiting_nodes[node.id] = signal.context
        if signal.scope == APPROVAL_SCOPE_ALL:
            self.halt_all = True
        await self._persist_node(node, state)
        await self.emit("node.paused", nodeId=node.id, nodeType=node.type,
                        duration=state.duration, pauseScope=signal.scope,
                        approvalContext=signal.context)
        log.info("workflow awaiting approval exec={} node={} scope={}",
                 self.execution_id, node.id, signal.scope)
        return None

    def downstream_closure(self, node_id: str) -> list[str]:
        """node_id 的下游可达闭包（不含自身）。"""
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            nid = stack.pop()
            for e in self.graph.get_out_edges(nid) or []:
                t = e.target
                if t == node_id or t in seen:
                    continue
                seen.add(t)
                stack.append(t)
        return sorted(seen)

    async def cancel_downstream(self, node_id: str, reason: str,
                               reject_reply: str = "") -> list[str]:
        """审批「不同意」：把下游可达闭包整体取消（其余分支照常跑完）。

        与并行短路同一口径复用 _cancelled_nodes：_schedule 见到它就跳过，
        _is_reachable 判它不可达，汇合闸门因此不会死等被砍的分支。
        已 COMPLETED 的下游不回滚（本轮它先跑完了）；正在跑的节点由自身协程
        收尾，其下游再靠闭包登记拦住。整条流终态是 COMPLETED（部分取消）。
        """
        if reject_reply:
            self.reject_replies[node_id] = reject_reply
        targets: list[str] = []
        for nid in self.downstream_closure(node_id):
            state = self.node_states.get(nid)
            if state is not None and state.status == STATUS_COMPLETED:
                continue
            targets.append(nid)
            self._cancelled_nodes.add(nid)
            self._blocked_targets.pop(nid, None)
            node = self.graph.get_node(nid)
            if node is None:
                continue
            if state is None:
                # 尚未被调度过：补一个取消态并落库，否则执行详情里看不到「被拒」的节点。
                # 事件成对发（started 不带 input），前端才不会停在无响应状态。
                state = NodeState()
                self._order_counter += 1
                state.order = self._order_counter
                state.label = node.label
                state.nodeType = node.type
                state.status = STATUS_CANCELLED
                state.error = reason
                self.node_states[nid] = state
                await self.emit("node.started", nodeId=nid, nodeType=node.type)
                await self.emit("node.cancelled", nodeId=nid, duration=0, reason=reason)
            elif state.status == STATUS_RUNNING:
                state.status = STATUS_CANCELLED
                state.error = reason
                state.duration = int((time.monotonic() - state.started_at) * 1000)
                await self.emit("node.cancelled", nodeId=nid, duration=state.duration,
                                reason=reason)
            else:
                continue
            await self._persist_node(node, state)
        log.info("approval rejected exec={} node={} cancelled={}",
                 self.execution_id, node_id, targets)
        return targets

    async def apply_output_edits(self, node_id: str, edits: list) -> list:
        """审批表单的编辑回写：同时改 node_states[源].output 与 ctx 里的同源输出。

        两处都得改：前者是跨轮恢复的权威源（下轮与详情接口读它），后者是本轮
        下游取数的入口。只改一处会出现「本轮已生效、恢复后又变回旧值」。
        返回带旧值的 diff（审批审计）。
        """
        diff: list = []
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            src = edit.get("nodeId")
            var = edit.get("varName")
            if not src or not var:
                continue
            state = self.node_states.get(src)
            if state is None:
                log.warning("approval edit target has no state exec={} node={}",
                            self.execution_id, src)
                continue
            output = state.output if isinstance(state.output, dict) else {}
            old = output.get(var)
            output[var] = edit.get("value")
            state.output = output
            self.ctx.set_node_output(src, output)
            diff.append({"nodeId": src, "varName": var,
                         "oldValue": old, "newValue": edit.get("value")})
        return diff

    async def _persist_state(self, paused: bool = False) -> None:
        """执行状态落库钩子(service 层注入,引擎不直接依赖 ORM)。"""
        if self.state_persist_hook:
            try:
                await self.state_persist_hook(self, paused)
            except Exception as e:  # noqa: BLE001
                log.warning("state persist failed exec={}: {}", self.execution_id, e)

    def _collect_outputs(self) -> dict:
        """收集 END 节点输出（多 END 合并；无 END 则空）；
        并按执行顺序合并 REPLY 指定回复节点的输出（后执行的覆盖先执行的），
        使回复内容直接出现在 workflow.completed 的 outputs 中。

        只认本轮真跑过的节点（_executed_this_round）：再提交时 node_states 里还留着
        上一轮的输出，全量收集会把上一轮的 END/REPLY 答案当成本轮回答吐给用户。
        本轮 END/REPLY 被审批「不同意」砍掉时，用审批节点配的拒绝文案兜底。
        """
        outputs = {}
        ordered = sorted((nid for nid in self._executed_this_round
                          if nid in self.node_states),
                         key=lambda n: self.node_states[n].order)
        for nid in ordered:
            node = self.graph.get_node(nid)
            state = self.node_states[nid]
            if node is None or state.status != STATUS_COMPLETED:
                continue
            if node.type in ("END", "REPLY"):
                output = self.ctx.get_node_output(nid)
                if isinstance(output, dict):
                    outputs.update(output)
        if not outputs and self.reject_replies:
            # 审批「不同意」把下游 END/REPLY 全砍了：没有回答会让对话页空白，
            # 用节点配的拒绝文案按 END 声明的变量名兜底铺回去
            reply = list(self.reject_replies.values())[-1]
            names = {f.get("name") for end in self.graph.find_end_nodes()
                     for f in ((end.data or {}).get("outputs") or []) if f.get("name")}
            names |= {"answer", "output", "text"}
            outputs = {name: reply for name in names if name}
        return outputs

    def node_states_dict(self) -> dict:
        return {nid: s.to_dict() for nid, s in self.node_states.items()}

    def snapshot(self) -> dict:
        """执行快照（写 tb_workflow_execution.variables，仅 PAUSED 与终态）。

        瘦身：去掉 context/completedWithBranch 等与 nodeStates 重复的字段——恢复不
        再依赖快照（以 node_states 为权威源由 hydrate 重建 ctx），快照只用于排障与
        详情展示，冗余副本只会在跨轮覆盖时造成口径不一致。
        """
        return {
            "executionId": self.execution_id,
            "status": self.status,
            "submitMode": self.submit_mode,
            "round": self.round,
            "nodeStates": self.node_states_dict(),
            "awaitingNodeIds": list(self.awaiting_nodes),
            "approvalContext": self.awaiting_nodes,
            "approvalDecisions": self.approval_decisions,
            "rejectReplies": self.reject_replies,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "llmCallCount": self.llm_call_count,
            "durationMs": self.duration_ms,
        }

    def hydrate(self, node_states: Optional[dict], replay_global: bool = True) -> None:
        """用 DB 里的 node_states（跨轮权威源）重建运行时上下文。

        重建三件事：
        1. NodeState 全字段（含 branch/llmMessages/review*）；
        2. ctx.node_outputs —— CONTINUE 模式下 skip 节点的下游要能引用到上轮结果；
        3. 出边端口 —— 汇合闸门与分支路由靠它判断跳过之后往哪个后继走。

        replay_global：只有 CONTINUE 才重放全局变量。RETRY 会把全部节点重跑一遍，
        带着上一轮的循环计数（如 loopIndex=3）开跑会让 LOOP 首轮就命中退出条件、
        循环体一次都不执行。
        """
        for nid, s in (node_states or {}).items():
            if not isinstance(s, dict) or self.graph.get_node(nid) is None:
                continue  # 画布已删掉的节点不重建（graph_hash 已拦住大部分，此处兜底）
            state = NodeState()
            state.order = int(s.get("order") or 0)
            state.status = s.get("status") or STATUS_COMPLETED
            state.input = s.get("input")
            state.output = s.get("output")
            state.error = s.get("error")
            state.duration = int(s.get("duration") or 0)
            state.label = s.get("label")
            state.nodeType = s.get("nodeType")
            state.branch = s.get("branch")
            state.llmMessages = list(s.get("llmMessages") or [])
            state.review = s.get("review")
            state.reviewBy = s.get("reviewBy")
            state.reviewOpinion = s.get("reviewOpinion")
            state.reviewDiff = s.get("reviewDiff")
            # skip 是本轮属性，不随历史状态带过来
            self.node_states[nid] = state
        self._order_counter = max((s.order for s in self.node_states.values()), default=0)
        for nid, state in self.node_states.items():
            # 只把「有结果的状态」灌回 ctx：RETRY 已把全部状态刷成 PENDING，
            # 再灌上轮输出会让未重跑的节点读到陈旧数据
            if (isinstance(state.output, dict) and state.output
                    and state.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_AWAITING)):
                self.ctx.set_node_output(nid, state.output)
            if state.status in (STATUS_COMPLETED, STATUS_FAILED):
                self._completed_with_branch[nid] = state.branch
        if replay_global:
            self._replay_global_vars()

    def _replay_global_vars(self) -> None:
        """按执行顺序重放全局变量（快照不再存 global 副本，靠已落库节点输出推导）。

        目前只有两类节点写 global_vars：
        - VARIABLE_ASSIGNER：output 里就是本轮被赋值的变量（无透传字段），全部重放；
        - LOOP：循环计数变量（执行器已按要求一并写进 output），按节点配置名精确取。
        新增写全局变量的节点类型时必须在这里补一条，否则恢复后变量会丢。
        """
        for nid, state in sorted(self.node_states.items(), key=lambda kv: kv[1].order):
            node = self.graph.get_node(nid)
            if node is None or state.status != STATUS_COMPLETED:
                continue
            output = state.output
            if not isinstance(output, dict):
                continue
            if node.type == "VARIABLE_ASSIGNER":
                self.ctx.global_vars.update(output)
            elif node.type == "LOOP":
                loop_var = (node.data or {}).get("loopVariable") or "loopIndex"
                if loop_var in output:
                    self.ctx.global_vars[loop_var] = output[loop_var]

    def restore(self, snapshot: dict) -> None:
        """从上一轮落库的 variables 快照重建运行时（提交接口投递前调用）。

        nodeStates 是权威源；老快照里的 context/global 只作结构升级前的兼容兜底。
        """
        self.hydrate(snapshot.get("nodeStates") or {},
                     replay_global=(self.submit_mode == SUBMIT_MODE_CONTINUE))
        legacy_ctx = snapshot.get("context") or {}
        if legacy_ctx:
            self.ctx.restore(legacy_ctx)
        legacy_global = snapshot.get("global")
        if legacy_global:
            merged = dict(self.ctx.global_vars)
            merged.update(legacy_global)
            self.ctx.global_vars = merged
        elif legacy_ctx.get("global"):
            self.ctx.global_vars = dict(legacy_ctx["global"])
        # 旧快照的端口路由记在顶层 completedWithBranch 里，nodeState.branch 缺失时回填
        for nid, b in (snapshot.get("completedWithBranch") or {}).items():
            state = self.node_states.get(nid)
            if state is not None and state.branch is None:
                state.branch = b
                if state.status in (STATUS_COMPLETED, STATUS_FAILED):
                    self._completed_with_branch[nid] = b
        self.input_tokens = int(snapshot.get("inputTokens", 0))
        # 老快照没有 round：保持构造参数（默认 1），别把它归零
        self.round = max(self.round, int(snapshot.get("round") or 0))
        self.output_tokens = int(snapshot.get("outputTokens", 0))
        self.llm_call_count = int(snapshot.get("llmCallCount", 0))
        self.reject_replies = dict(snapshot.get("rejectReplies") or {})
        self.duration_base_ms = int(snapshot.get("durationMs", 0))
        # 提交时预存的审批结论：构造参数已带时不覆盖（新结论优先于快照里的残留）
        if not self.approval_decisions:
            self.approval_decisions = dict(snapshot.get("approvalDecisions") or {})
