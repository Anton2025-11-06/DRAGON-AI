# -*- coding: utf-8 -*-
"""tb_workflow_execution 这一行的唯一读写口：状态、挂起代次、两段跨轮状态都只从这里过。

读：详情、订阅分流、worker 重建运行时。写：提交抢占、worker 收尾与挂起、唤醒。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import delete, update

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.execution.pause_state import PAUSE_STATE_KEY, PauseState
from service.service_workflow.execution.round_request import (
    ROUND_REQUEST_KEY, RoundRequest,
)
from service.service_workflow.models.workflow_entity import (
    WorkflowExecution, WorkflowNodeExecution,
)
from service.service_workflow.workflow_engine.engine import (
    STATUS_CANCELLED, STATUS_PAUSED, STATUS_PENDING, STATUS_RUNNING,
    SUBMIT_MODE_CONTINUE, TERMINAL_STATUSES,
)

# 代次只在这些时刻推进一次：有人开跑（含审批后的唤醒）。挂起本身不推进——
# 同一次运行里发出的所有帧必须带同一个代次，否则收尾的挂起帧会被判成过期帧。
RUNNING_STATUSES = (STATUS_RUNNING, STATUS_PENDING)

# 新执行的第一代：它没有上一轮，也就不需要靠代次区分迟到的帧
FIRST_GENERATION = 1


class ClaimLost(ValueError):
    """没抢到这一行的本轮执行权：并发里另一次提交已把它推成待跑。

    单独立类只为让提交路径认出「抢输」这一种并回成 409；它仍是 ValueError 的子类，
    行缺失等其它 ValueError 不会被误归到抢输上。
    """


@dataclass
class NewRun:
    """新开一行执行要落的首轮事实：归属与触发方式、这一版图的指纹、本轮要消费的输入。"""

    workflow_id: int
    workflow_version: int
    trigger_type: str
    graph_hash: str
    inputs: dict
    round_request: RoundRequest
    user_id: int = 0
    parent_exec_id: Optional[str] = None
    parent_node_id: Optional[str] = None


@dataclass
class RunClaim:
    """提交抢占的输入：本轮怎么跑、跑完前要清掉哪些上轮现场。"""

    mode: str
    graph_hash: str
    round_request: RoundRequest
    node_states: dict
    inputs: Optional[dict] = None
    restarts_statistics: bool = False


@dataclass
class WatchState:
    """订阅与详情判定要的三份事实：行状态、挂起代次、还欠谁的审批。"""

    status: Optional[str]
    pause_generation: int
    pause_state: PauseState

    @property
    def exists(self) -> bool:
        return self.status is not None

    @property
    def awaiting_human(self) -> bool:
        return self.pause_state.is_awaiting_human()

    @property
    def awaiting_resume(self) -> bool:
        """本轮已跑完一段、只欠一次唤醒（结论已交给子执行时就是这个形状）。"""
        return self.status == STATUS_PAUSED and not self.awaiting_human

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass
class RunResultFacts:
    """worker 一轮跑完（或在审批节点挂起）后要写回这一行的事实。"""

    status: str
    submit_mode: Optional[str]
    node_states: dict
    outputs: Optional[dict] = None
    error_message: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_call_count: int = 0
    duration_ms: int = 0
    current_node_id: Optional[str] = None
    awaiting_node_ids: list[str] = field(default_factory=list)
    pause_state: Optional[PauseState] = None


@dataclass
class SubmitContext:
    """判定一次提交所需的一行现状：状态、当时那版图的指纹、还欠谁的审批。

    图本体不住这里：取图要回看发布版本，那是调用方的事（`load_graph` 注进 submit_resolver）。
    """

    execution_id: str
    workflow_id: int
    workflow_version: int
    status: Optional[str]
    stored_graph_hash: str
    inputs: dict
    node_states: dict
    round_no: int
    pause: PauseState

    @property
    def is_running(self) -> bool:
        return self.status in RUNNING_STATUSES

    @property
    def open_approvals(self) -> list:
        """还欠人答的待办；为空即这一轮没人挡路。"""
        return self.pause.open_approvals()


class ExecutionStateStore:
    """执行行的读写收口：本文件之外不允许出现对 variables 顶层与 status 的直接写。"""

    # ---------- 两段式 JSON 的编解码（纯函数，可离线断言） ----------

    @staticmethod
    def encode_variables(round_request: RoundRequest,
                         pause_state: PauseState) -> dict:
        """拼 variables 列：本轮要消费什么 + 此刻在等谁（没在等就是空清单）。"""
        return {ROUND_REQUEST_KEY: round_request.to_dict(),
                PAUSE_STATE_KEY: pause_state.to_dict()}

    @staticmethod
    def decode_variables(data: Any, pause_generation: int = 1) -> tuple:
        """拆 variables 列，返回 (RoundRequest, PauseState)；缺段按空事实处理。"""
        payload = data if isinstance(data, dict) else {}
        return (RoundRequest.from_dict(payload.get(ROUND_REQUEST_KEY)),
                PauseState.from_dict(payload.get(PAUSE_STATE_KEY), pause_generation))

    # ---------- 读 ----------

    @staticmethod
    async def read_watch_state(execution_id: str) -> WatchState:
        """取订阅判定要的三份事实；行不存在时 status 为 None。

        挂起清单走 pause_state_of_row 同一份口径：行不在 PAUSED 就没有欠着的审批，
        订阅端与详情页不会对同一行读出两种答案。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return WatchState(None, 1, PauseState())
            pause = ExecutionStateStore.pause_state_of_row(row)
            return WatchState(row.status, pause.pause_generation, pause)

    @staticmethod
    async def read_round_request(execution_id: str) -> RoundRequest:
        """取本轮待消费事实（worker 重建运行时用）；读后即弃由收尾那次写回完成。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return RoundRequest()
            variables = row.variables
            generation = int(row.pause_generation or 1)
        request, _pause = ExecutionStateStore.decode_variables(variables, generation)
        return request

    @staticmethod
    def pause_state_of_row(row: WorkflowExecution) -> PauseState:
        """从已取到的执行行取这份挂起事实；行不是 PAUSED 就是空清单。

        跑动中或已收尾的行不可能欠着审批：留着旧清单会让详情弹出一份已经过了的待办。
        """
        generation = int(row.pause_generation or 1)
        if row.status != STATUS_PAUSED:
            return PauseState(generation)
        _request, pause = ExecutionStateStore.decode_variables(row.variables, generation)
        return pause

    @staticmethod
    async def read_pause_state(execution_id: str) -> PauseState:
        """取这份挂起事实（详情投影与唤醒/投递判定用）；行不存在给空清单。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return PauseState()
            return ExecutionStateStore.pause_state_of_row(row)

    @staticmethod
    async def read_submit_context(execution_id: str) -> Optional[SubmitContext]:
        """取这条执行现在记着什么（提交判定用）；行不存在返回 None。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            generation = int(row.pause_generation or 1)
            request, pause = ExecutionStateStore.decode_variables(
                row.variables, generation)
            return SubmitContext(
                execution_id=row.id, workflow_id=row.workflow_id,
                workflow_version=row.workflow_version, status=row.status,
                stored_graph_hash=row.graph_hash or "", inputs=dict(row.inputs or {}),
                node_states=dict(row.node_states or {}), round_no=request.round_no,
                pause=pause if row.status == STATUS_PAUSED else PauseState(generation))

    # ---------- 写 ----------

    @staticmethod
    async def create_for_run(execution_id: str, run: NewRun) -> int:
        """插一行正在跑的新执行（第一轮），返回它的挂起代次。

        submit_mode 留空——空即「这行还没被跑过」，worker 重建运行时据此不走恢复分支。
        """
        async with mysql_client.get_session() as session:
            session.add(WorkflowExecution(
                id=execution_id, workflow_id=run.workflow_id,
                workflow_version=run.workflow_version, status=STATUS_RUNNING,
                trigger_type=run.trigger_type, inputs=run.inputs,
                variables=ExecutionStateStore.encode_variables(
                    run.round_request, PauseState(FIRST_GENERATION)),
                graph_hash=run.graph_hash, pause_generation=FIRST_GENERATION,
                user_id=run.user_id, parent_exec_id=run.parent_exec_id,
                parent_node_id=run.parent_node_id, started_at=datetime.now()))
            await session.commit()
        return FIRST_GENERATION

    @staticmethod
    async def claim_for_run(execution_id: str, claim: RunClaim) -> int:
        """把一条非运行中的执行抢成本轮要跑的状态，返回新挂起代次。

        抢占是一条带 status 条件的 UPDATE：并发提交只有一边改得动，抢输抛 ClaimLost。
        写 status/submit_mode/graph_hash/awaiting_node_id/inputs（给了才写）/
        roundRequest，并把代次 +1；RETRY 另归零统计与耗时、重置 started_at；
        同时清 outputs/error_message/completed_at/current_node_id 与上一轮节点明细。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                raise ValueError("执行记录不存在")
            new_generation = int(row.pause_generation or 1) + 1
            upd = await session.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id,
                       WorkflowExecution.status.notin_(list(RUNNING_STATUSES)))
                .values(status=STATUS_RUNNING, submit_mode=claim.mode,
                        graph_hash=claim.graph_hash, awaiting_node_id=None,
                        pause_generation=new_generation))
            if upd.rowcount != 1:
                await session.rollback()
                raise ClaimLost("他人正在提交，请勿重复提交相同任务")
            row = await session.get(WorkflowExecution, execution_id)
            row.node_states = claim.node_states
            row.variables = ExecutionStateStore.encode_variables(
                claim.round_request, PauseState(new_generation))
            row.outputs = None
            row.error_message = None
            row.completed_at = None
            row.current_node_id = None
            if claim.inputs is not None:
                row.inputs = claim.inputs
            if claim.restarts_statistics:
                # 重新执行是新一轮统计：不能累加上轮的 token，也不能带着上轮耗时起步
                row.input_tokens = row.output_tokens = row.llm_call_count = 0
                row.duration_ms = 0
                row.started_at = datetime.now()
            # 节点明细按轮重建：不删就会把上一轮的行与本轮的 skip/重跑行混在一起
            await session.execute(delete(WorkflowNodeExecution)
                                  .where(WorkflowNodeExecution.execution_id == execution_id))
            await session.commit()
        return new_generation

    @staticmethod
    async def save_run_result(execution_id: str, facts: RunResultFacts) -> None:
        """落 worker 本轮结果（节点边界/挂起/终态时调用，全部串行 await）。

        两段式 variables 只在挂起与终态写：每个节点边界都刷一份大 JSON 既没必要又拖慢引擎。
        写回时本轮结论已消费，只保留轮次；挂起写 facts 给的清单，终态一律清空待办。
        """
        persist_variables = (facts.status == STATUS_PAUSED
                             or facts.status in TERMINAL_STATUSES)
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return
            row.status = facts.status
            row.node_states = facts.node_states
            row.outputs = facts.outputs
            row.error_message = facts.error_message
            row.input_tokens = facts.input_tokens
            row.output_tokens = facts.output_tokens
            row.llm_call_count = facts.llm_call_count
            row.duration_ms = facts.duration_ms
            row.current_node_id = facts.current_node_id
            row.submit_mode = facts.submit_mode
            row.awaiting_node_id = (facts.awaiting_node_ids[0]
                                    if facts.awaiting_node_ids else None)
            if persist_variables:
                generation = int(row.pause_generation or 1)
                request, _pause = ExecutionStateStore.decode_variables(
                    row.variables, generation)
                pause = (facts.pause_state if facts.status == STATUS_PAUSED
                         else PauseState(generation))
                row.variables = ExecutionStateStore.encode_variables(
                    request.with_decisions_consumed(), pause)
            if facts.status in TERMINAL_STATUSES:
                row.completed_at = datetime.now()
            await session.commit()

    @staticmethod
    async def change_pause_state(execution_id: str,
                                apply_change: Callable[[PauseState], Any],
                                only_while_paused: bool = True) -> Optional[PauseState]:
        """在同一事务里改这份挂起事实再写回；回调返回假值即不落库。

        :param only_while_paused: 行已不在 PAUSED（终态或已被推起）时不写，
            免得把没人读的旧清单挂到别的状态上。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            if only_while_paused and row.status != STATUS_PAUSED:
                return None
            generation = int(row.pause_generation or 1)
            request, pause = ExecutionStateStore.decode_variables(row.variables, generation)
            if not apply_change(pause):
                return None
            row.variables = ExecutionStateStore.encode_variables(request, pause)
            await session.commit()
            return pause

    @staticmethod
    async def wake_to_running(execution_id: str,
                             expected_generation: Optional[int] = None,
                             reset_node_ids: Optional[list] = None) -> int:
        """把这条执行推回 RUNNING 并推进代次，返回新代次；抢不到返回 0。

        同一事务里清掉本轮不再有效的事实：上轮结论与待办清单，以及 reset_node_ids
        那几份节点状态（退回 PENDING 且不 skip，本轮由节点执行器自己重新收尾）。
        代次不符即抢不到：说明已经有人在推，或这条执行已经换过一轮。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_PAUSED:
                return 0
            generation = int(row.pause_generation or 1)
            if expected_generation is not None and generation != int(expected_generation):
                return 0
            new_generation = generation + 1
            upd = await session.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id,
                       WorkflowExecution.status == STATUS_PAUSED,
                       WorkflowExecution.pause_generation == generation)
                .values(status=STATUS_RUNNING, submit_mode=SUBMIT_MODE_CONTINUE,
                        awaiting_node_id=None, pause_generation=new_generation))
            if upd.rowcount != 1:
                await session.rollback()
                return 0
            row = await session.get(WorkflowExecution, execution_id)
            request, _pause = ExecutionStateStore.decode_variables(
                row.variables, new_generation)
            row.variables = ExecutionStateStore.encode_variables(
                request.with_decisions_consumed(), PauseState(new_generation))
            node_states = dict(row.node_states or {})
            for node_id in reset_node_ids or []:
                state = node_states.get(node_id)
                if isinstance(state, dict):
                    node_states[node_id] = dict(state, status=STATUS_PENDING, skip=False)
            row.node_states = node_states
            await session.commit()
        return new_generation

    @staticmethod
    async def mark_cancelled(execution_id: str) -> bool:
        """把这条执行判成 CANCELLED，返回有没有改到这一行。

        只接 RUNNING / PAUSED 两种可取消状态：已收尾的行不该再被改。停在审批的那份
        坐标一起清掉，completed_at 必须在这补——PAUSED 时没有 worker 在收尾，不补它
        就会停在 PAUSED 永远可提交（RUNNING 时引擎收尾会再覆写一次，写重了也无害）。
        带 status 条件做 UPDATE：与并发的收尾抢写时谁先改到谁算，不会把终态盖成取消。
        """
        async with mysql_client.get_session() as session:
            upd = await session.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id,
                       WorkflowExecution.status.in_([STATUS_RUNNING, STATUS_PAUSED]))
                .values(status=STATUS_CANCELLED, awaiting_node_id=None,
                        completed_at=datetime.now()))
            await session.commit()
            return upd.rowcount == 1

    @staticmethod
    async def rollback_claim(execution_id: str, prev_status: str,
                             error_message: Optional[str] = None) -> None:
        """投递失败回滚行状态：不回滚这条执行会永久卡在 RUNNING（既不能再提交，
        worker 也不会跑它）。代次不回退：代次只表示「有没有新帧该被认出来」。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_RUNNING:
                return
            row.status = prev_status
            row.error_message = error_message
            if prev_status in TERMINAL_STATUSES:
                # 抢占时清掉的收尾时间要补回来：判了终态却没有结束时间，列表页算不出耗时
                row.completed_at = datetime.now()
            await session.commit()
        log.warning("submit rolled back exec={} -> {}", execution_id, prev_status)
