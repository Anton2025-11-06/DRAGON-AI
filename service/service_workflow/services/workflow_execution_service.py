# -*- coding: utf-8 -*-
"""工作流执行服务:一次提交怎么落到执行行上、怎么跑起来、跑完怎么对外。

跨进程交接只有 MySQL 那一行：API 进程写下「本轮要什么」，arq worker 读它跑一轮再写回。
提交判定在 submit_resolver，执行行的读写在 ExecutionStateStore，这里只剩编排。
- 执行本体在独立的 arq worker 进程（每进程自带事件循环），前端凭 executionId 走
  subscribe 接口跨进程订阅（Redis Pub/Sub）。
- 取消由执行记录表状态驱动:API 层把行判成 CANCELLED（只经 ExecutionStateStore）→
  engine 每节点执行前 status_check_hook 查 DB,读到 CANCELLED 即收尾。
- 节点明细/执行状态通过 runtime 钩子实时落库，全部 await 串行：火忘的迟到写会把
  上一轮数据盖到新一轮上。

对外只有一个 submit 动作与一份统一出参，契约与模块边界见 docs/workflow-execution-contract.md；
「此刻欠谁」的业务口径见 docs/workflow-approval-memory.md。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from functools import partial
from typing import Any, Optional

from sqlalchemy import func, select
from fastapi import WebSocket

from common.common_arq.queue import enqueue_job, next_split_number
from common.common_exception.custom_exception import WorkflowGraphError
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.execution import (
    approval_projection, event_frames, execution_tree,
)
from service.service_workflow.execution.execution_state import (
    ClaimLost, ExecutionStateStore, NewRun, RunClaim, RunResultFacts,
)
from service.service_workflow.execution.execution_tree import wake_parent_execution
from service.service_workflow.execution.pause_state import PauseState
from service.service_workflow.execution.round_request import RoundRequest
from service.service_workflow.execution.submit_resolver import (
    SubmitPlan, SubmitRejected, resolve_child_submit, resolve_submit,
)
from service.service_workflow.models.workflow_entity import (
    Workflow, WorkflowExecution, WorkflowNodeExecution, WorkflowApiKey,
)
from service.service_workflow.schemas.workflow_schema import WorkflowSubmitReq
from service.service_workflow.services.event_pubsub import (
    publish_event_hook, subscribe_event_channel,
    use_ws_event_scope, ws_in_scope,
)
from service.service_workflow.workflow_engine.nodes.approval_nodes import (
    seed_approver_identity,
)
from service.service_workflow.workflow_engine.engine import (
    STATUS_COMPLETED, STATUS_FAILED, STATUS_PAUSED, STATUS_RUNNING,
    SUBMIT_MODE_CONTINUE, SUBMIT_MODE_RETRY, WorkflowRuntime, compute_graph_hash,
)
from service.service_workflow.workflow_engine.events import EventBus
from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.model_client import (
    ModelConfigProvider,
)
from common.common_httpx.httpx import httpx_pool


# 投递选片(按切片数轮询分发):切片数配置在 arq Redis 的 workflow_queue:split_number
# (不存在默认 1,由 system 监控页修改),按 execution_id 的 crc32 取模选切片队列
# (workflow_queue:split_{N});同一执行稳定落同一切片,见 common_arq.queue.next_split_number。


def get_shared_http():
    return httpx_pool.client


# 父侧取子执行结果时不参与收集的节点类型（只留在 row.node_states 里供详情展示）：
# - START：入参回显，父侧自己传进去的（row.inputs 已有一份）；
# - END/REPLY：它们的输出就是 workflow outputs 本体，顶层已经平铺了一份；
# - IF_ELSE/PARALLEL：执行器把入边上游输出整块透传进自己的 output（见各节点的
#   input_pass_through），收进来全是重复；
# - LOOP：output 里的 loopResult 就是循环体子图的全部节点输出，而体内节点会被逐个单独收。
# ITERATION 不在列：它的 items 是逐次迭代的聚合结果，循环体每轮会被 _reset_subgraph_nodes
# 重置，体内节点的 output 只剩最后一轮，只有这个聚合键是完整的。
# 新增节点类型时要想清楚它属不属于这一类，否则父侧（【工作流】节点、LLM 的工作流工具）
# 会白吃一份重复数据。
CHILD_RESULT_SKIP_TYPES = frozenset(
    {"START", "END", "REPLY", "IF_ELSE", "LOOP", "PARALLEL"})


def child_result_outputs(row: WorkflowExecution) -> dict:
    """子执行对外的输出（只给父侧取数用，不改 row.outputs 的公开语义）。

    两部分并到一层：各业务节点自己的输出（键 = 节点 id）+ 子流程 END/REPLY 声明的
    outputs（顶层平铺，优先级最高 —— 它是子流程对外的公开契约，不能被节点输出盖掉）。
    子流程的 END 可以一行输出都不配（END_NO_OUTPUTS 只是 WARNING），只给 row.outputs
    父侧会拿到空 dict —— 等于让模型拿着一个空工具返回值继续编。

    参不参与的三条过滤跟事件广播同口径（见 engine.NodeState.emitted）：
    - status 为 COMPLETED / FAILED：失败节点也要给，它的产出就是那行 error
      （失败分支上 state.output 被引擎显式置 None，只有 exception 端口情形会写回 ctx，
      拿 output 等于什么都不给）；
    - skip=false：CONTINUE 轮被跳过的上轮节点不算本轮结果；
    - emitted=true：节点「返回内容」开关关掉的对外不给（存量行缺这个字段按开处理）。

    节点类型在 CHILD_RESULT_SKIP_TYPES 里的一律不收（入参回显 / 顶层已平铺 / 整块透传）。
    """
    outputs = row.outputs if isinstance(row.outputs, dict) else {}
    merged: dict = {}
    if row.status == STATUS_COMPLETED:
        states = sorted(
            ((nid, st) for nid, st in (row.node_states or {}).items()
             if isinstance(st, dict)),
            # 按执行顺序：id 撞车时（理论上只有 END 变量名 vs 节点 id）后面的节点覆前面的
            key=lambda kv: int(kv[1].get("order") or 0))
        for nid, st in states:
            if st.get("nodeType") in CHILD_RESULT_SKIP_TYPES:
                continue
            status = st.get("status")
            if status not in (STATUS_COMPLETED, STATUS_FAILED) or st.get("skip"):
                continue
            if st.get("emitted", True) is False:
                continue
            if status == STATUS_FAILED:
                error = st.get("error")
                output = {"error": error} if error else None
            else:
                output = st.get("output")
            if not isinstance(output, dict) or not output:
                continue
            merged[nid] = output
    merged.update(outputs)
    return merged


# 生产环境模型配置源(Redis 缓存 + MySQL 回源)
_model_provider: Optional[ModelConfigProvider] = None


def get_model_provider():
    global _model_provider
    if _model_provider is None:
        from common.common_redis.redis import client as redis_client
        _model_provider = ModelConfigProvider(
            redis_client=getattr(redis_client, "client", redis_client),
            mysql_client=mysql_client)
    return _model_provider


class WorkflowExecutionService:

    # ==================== 提交（API 进程：判定 → 落库 → 驱动） ====================

    @staticmethod
    async def submit(req: WorkflowSubmitReq, *, websocket: Optional[WebSocket] = None,
                     user_id: int = 0, trigger_type: str = "API",
                     retry_times: int = 3) -> dict:
        """把一次提交落到执行行上并让它跑起来，返回对外统一的那份结果。

        :param websocket: 给了就在本进程跑并把事件回推这条连接（预览页），不给则投队列
        :raises SubmitRejected: 这次提交不被允许（文案与业务码直接面向调用方）
        新建、答审批、追问、重开共用一条路：判定在 submit_resolver，落库在
        ExecutionStateStore，这里只剩「插一行 / 抢占原行」两种起点与「投队列 /
        本进程跑」两种驱动。
        """
        plan = await resolve_submit(req, load_graph=WorkflowExecutionService.load_graph,
                                    user_id=user_id)
        if plan.is_new:
            plan.execution_id = await WorkflowExecutionService._create_run(
                plan, user_id=user_id, trigger_type=trigger_type)
        return await WorkflowExecutionService._run_plan(
            plan, websocket=websocket, user_id=user_id, retry_times=retry_times)

    @staticmethod
    async def _run_plan(plan: SubmitPlan, *, websocket: Optional[WebSocket],
                        user_id: int, retry_times: int) -> dict:
        """落一个提交计划，返回这一行的现状；本级不跑的就只剩转给子级那一种。"""
        if not plan.runs_here:
            if plan.duplicated:
                return await WorkflowExecutionService._submit_result(
                    plan.execution_id, duplicated=True)
            return await WorkflowExecutionService._forward_runs(
                plan, websocket=websocket, user_id=user_id, retry_times=retry_times)
        if not plan.is_new:
            try:
                await ExecutionStateStore.claim_for_run(plan.execution_id, RunClaim(
                    mode=plan.mode, graph_hash=plan.graph_hash,
                    round_request=RoundRequest(round_no=plan.round_no,
                                               decisions=plan.decisions),
                    node_states=plan.node_states, inputs=plan.values,
                    restarts_statistics=plan.restarts))
            except ClaimLost as e:
                # 抢这一下输了：并发里另一次提交已把行推成待跑，同一份结论不该转两遍
                raise SubmitRejected(str(e), 409) from e
        try:
            await WorkflowExecutionService._drive(plan.execution_id, websocket, retry_times)
        except ValueError:
            # 行已经是 RUNNING：不回退就既跑不了又提交不了；新建那行没有前一个状态，只能判失败
            await ExecutionStateStore.rollback_claim(
                plan.execution_id, plan.prev_status or STATUS_FAILED, "任务投递失败")
            raise
        return await WorkflowExecutionService._submit_result(plan.execution_id)

    @staticmethod
    async def _forward_runs(plan: SubmitPlan, *, websocket: Optional[WebSocket],
                            user_id: int, retry_times: int) -> dict:
        """把结论交给正被等的子执行去跑，本级保持暂停，等子执行收尾时把自己推起来。

        子执行不拿这条连接当事件出口：它的节点 id 不在本级图里，混进来会把调试面板
        画乱；只借这层范围让本级后续的续跑事件找得回路。
        """
        with use_ws_event_scope(websocket):
            for run in plan.forwards:
                child = await resolve_child_submit(
                    run.execution_id, run.decisions,
                    load_graph=WorkflowExecutionService.load_graph, user_id=user_id)
                await WorkflowExecutionService._run_plan(
                    child, websocket=None, user_id=user_id, retry_times=retry_times)
                await WorkflowExecutionService._mark_delivered(
                    plan.execution_id, run.approval_tokens, run.execution_id)
        return await WorkflowExecutionService._submit_result(plan.execution_id)

    @staticmethod
    async def _drive(execution_id: str, websocket: Optional[WebSocket],
                     retry_times: int) -> None:
        """让这条已经摆成待跑的执行真的跑起来：本进程跑，或投队列并等它被接走。"""
        if websocket is not None or ws_in_scope() is not None:
            await WorkflowExecutionService.run_in_worker(execution_id=execution_id,
                                                         websocket=websocket)
            return
        for _ in range(max(1, retry_times)):
            if await enqueue_job("arq_tasks.tasks.workflow.execute_workflow", [execution_id],
                                 job_id=execution_id,
                                 split_number=await next_split_number(execution_id)):
                return
            await asyncio.sleep(1)
        raise ValueError("暂时无法提交，任务尚未彻底结束，请稍后再试")

    @staticmethod
    async def _submit_result(execution_id: str, *, duplicated: bool = False) -> dict:
        """拼 submit 的统一返回体：状态、代次、还欠着的那几道审批，全从这一行现状读。"""
        watch = await ExecutionStateStore.read_watch_state(execution_id)
        return {"executionId": execution_id, "status": watch.status,
                "pauseGeneration": watch.pause_generation, "duplicated": duplicated,
                "pendingApprovals": approval_projection.public_approvals(watch.pause_state)}

    @staticmethod
    async def _mark_delivered(execution_id: str, approval_tokens: list,
                              child_execution_id: str) -> None:
        """把已经转出去的待办标成「结论已交给这条子执行」，本级状态不动。

        写在转发**成功之后**：先写后转的话，转发失败会留下一个永远等不来的标记，
        消费方就要白等一轮才退回原口径。本级已不在 PAUSED 就不写。
        """

        def mark(pause: PauseState) -> bool:
            changed = False
            for token in approval_tokens:
                changed = pause.mark_delivered(token, child_execution_id) or changed
            return changed

        await ExecutionStateStore.change_pause_state(execution_id, mark)

    @staticmethod
    async def _create_run(plan: SubmitPlan, *, user_id: int,
                          trigger_type: str) -> str:
        """新开一行执行并摆成第一轮要跑的样子，返回它的 executionId。

        取图在这里分岔：预览跑草稿，对外调用只认已发布版本（先发布再执行）。
        """
        async with mysql_client.get_session() as session:
            wf = await session.get(Workflow, plan.workflow_id)
            if wf is None:
                raise SubmitRejected("工作流不存在")
            version = 0
            if trigger_type != "DEBUG":
                if wf.current_version <= 0:
                    raise SubmitRejected("工作流尚未发布，请先发布后再执行")
                version = wf.current_version
        graph_raw = await WorkflowExecutionService.load_graph(plan.workflow_id, version)
        if not graph_raw:
            raise SubmitRejected("工作流图为空，请先保存画布或发布后再执行")
        errors = [i for i in WorkflowGraph(graph_raw).validate() if i.severity == "ERROR"]
        if errors:
            raise WorkflowGraphError(errors)
        execution_id = str(uuid.uuid4())
        await ExecutionStateStore.create_for_run(execution_id, NewRun(
            workflow_id=plan.workflow_id, workflow_version=version,
            trigger_type=trigger_type, user_id=user_id,
            graph_hash=compute_graph_hash(graph_raw), inputs=plan.values or {},
            round_request=RoundRequest(round_no=1)))
        return execution_id

    # ==================== 执行入口(arq worker 进程:重建运行时 + 驱动) ====================

    @staticmethod
    async def run_in_worker(execution_id: str, websocket: Optional[WebSocket] = None) -> None:
        """执行工作流(execute_workflow 任务入口,运行在 arq worker 进程)。

        每一种提交都共用本入口与同一个 arq 任务函数,差异全在 DB 行里
        （submit_mode / node_states / variables 的 roundRequest 段）。

        状态守卫:只消费 DB 状态为 RUNNING 的记录——投递后/消费前用户可能已取消,
        或同一 executionId 被重复投递,此处拒绝才能避免「已终态的记录又被跑一遍」。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            status = row.status if row else None
        if status != STATUS_RUNNING:
            log.warning("workflow run skipped: exec={} status={} (not RUNNING)",
                        execution_id, status)
            return
        runtime = await WorkflowExecutionService._build_runtime(execution_id=execution_id, websocket=websocket)
        if runtime is None:
            log.warning("workflow run skipped: exec={} not found", execution_id)
            return
        try:
            await runtime.run()
        except Exception as e:  # noqa: BLE001
            log.error("workflow run failed exec={}: {}", execution_id, e)
        # 子执行收尾：本条若是【工作流】节点的子执行且已进终态，推一把等它的父执行。
        # 推进失败不能把子执行的收尾也拖成失败（子行状态已落库），只留痕：
        # 父行保持 PAUSED，靠人工再提交兜底。
        try:
            await WorkflowExecutionService._wake_parent(execution_id)
        except Exception as e:  # noqa: BLE001
            log.error("resume parent after child exec={} failed: {}", execution_id, e)

    @staticmethod
    async def load_graph(workflow_id: int, workflow_version: int) -> Optional[dict]:
        """取某一版的图：发布过的拿那一版快照，拿不到快照（含草稿执行）退回当前草稿。

        再提交与恢复必须用**当时那一版**：草稿已被改过上轮 node_states 里的 node_id
        就与新图对不上，skip 与路由会变成随机行为（画布漂移另有 graph_hash 校验兜底）。
        """
        if workflow_version > 0:
            from service.service_workflow.services.workflow_service import WorkflowService
            snap = await WorkflowService.get_snapshot(workflow_id, workflow_version)
            if snap:
                return snap
        async with mysql_client.get_session() as session:
            wf = await session.get(Workflow, workflow_id)
            return wf.graph if wf else None

    @staticmethod
    async def _build_runtime(execution_id: str,
                             websocket: Optional[WebSocket] = None) -> Optional[WorkflowRuntime]:
        """从 DB 执行记录重建运行时（arq worker 进程内调用）。

        节点状态的权威源是 row.node_states（由 hydrate 重建）；本轮要消费的审批结论
        与轮次从 variables 的 roundRequest 段读，挂起代次读 row.pause_generation 并
        盖进事件总线（同一次运行的全部帧同号，消费方据此丢过期帧）。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            # 取出所需字段后再离开 session：session 关闭后 ORM 属性不可读
            workflow_id, workflow_version = row.workflow_id, row.workflow_version
            variables = row.variables
            generation = int(row.pause_generation or 1)
            node_states = row.node_states or {}
            submit_mode = row.submit_mode or SUBMIT_MODE_RETRY
            inputs = dict(row.inputs or {})
            trigger_type, user_id = row.trigger_type, row.user_id
            usage = (int(row.input_tokens or 0), int(row.output_tokens or 0),
                     int(row.llm_call_count or 0))
            duration_ms = int(row.duration_ms or 0)

        graph_raw = await WorkflowExecutionService.load_graph(workflow_id, workflow_version)
        if not graph_raw:
            return None
        graph = WorkflowGraph(graph_raw)
        request, _pause = ExecutionStateStore.decode_variables(variables, generation)
        runtime = WorkflowRuntime(
            graph=graph, execution_id=execution_id,
            # 输入以执行记录行为准（提交接口已把本轮 inputs 写回该行）
            inputs=inputs,
            model_provider=get_model_provider(),
            http_client=get_shared_http(),
            # 事件总线：
            # API执行：节点事件(含 node.delta)经 pub hook 实时 PUBLISH 到 Redis 频道，供其他进程的 SSE 订阅者跨进程实时消费
            # DEBUG 执行：节点事件(含 node.delta)经 websocket 传输
            event_bus=EventBus(execution_id,
                               publish_hook=(partial(publish_event_hook, websocket=websocket)
                                             if websocket is not None else publish_event_hook),
                               pause_generation=generation),
            trigger_type=trigger_type,
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            submit_mode=submit_mode,
            # 当前对话轮次（提交接口已写好本轮值；首次执行 roundRequest 缺失 → 1）
            round_no=request.round_no,
            # 本轮携带的审批结论：提交接口写进 roundRequest，worker 消费即失效（不回灌）
            approval_decisions=request.decisions,
            node_persist_hook=WorkflowExecutionService._persist_node,
            state_persist_hook=WorkflowExecutionService._persist_state,
            status_check_hook=WorkflowExecutionService._execution_status,
        )
        if node_states:
            # RETRY 全量重跑:不重放全局变量（带着上轮循环计数会让 LOOP 首轮就退出）
            runtime.hydrate(node_states, replay_global=(submit_mode == SUBMIT_MODE_CONTINUE))
        if submit_mode == SUBMIT_MODE_RETRY:
            # 重新执行:统计与耗时从 0 起算(提交接口已把行归零,此处再兜一次)
            runtime.input_tokens = runtime.output_tokens = runtime.llm_call_count = 0
            runtime.duration_base_ms = 0
        else:
            runtime.input_tokens, runtime.output_tokens, runtime.llm_call_count = usage
            runtime.duration_base_ms = duration_ms
        # 工具类节点（TOOL / MCP_TOOL）由执行器直接调 service 层，不再经 runtime 钩子中转
        return runtime

    @staticmethod
    async def _persist_node(runtime: WorkflowRuntime, node, state) -> None:
        """节点状态变化落库（按 (execution_id, node_id, node_order) upsert）。

        终态也必须能插行：再提交的 skip 节点、审批挂起的 AWAITING 节点、被拒绕过
        但从没被调度过的下游节点，都**不经过 RUNNING**；只 UPDATE 会整行丢失，
        而明细表是执行详情页唯一数据源。
        """
        async with mysql_client.get_session() as session:
            existing = (await session.execute(
                select(WorkflowNodeExecution)
                .where(WorkflowNodeExecution.execution_id == runtime.execution_id,
                       WorkflowNodeExecution.node_id == node.id,
                       WorkflowNodeExecution.node_order == state.order)
            )).scalar_one_or_none()
            now = datetime.now()
            if existing is None:
                session.add(WorkflowNodeExecution(
                    execution_id=runtime.execution_id, node_id=node.id, node_type=node.type,
                    status=state.status, node_order=state.order, input=state.input,
                    output=state.output, error=state.error, duration_ms=state.duration,
                    started_at=now,
                    completed_at=None if state.status == STATUS_RUNNING else now))
            else:
                existing.status = state.status
                existing.node_type = node.type
                existing.input = state.input
                existing.output = state.output
                existing.error = state.error
                existing.duration_ms = state.duration
                if state.status == STATUS_RUNNING:
                    existing.started_at = now
                else:
                    existing.completed_at = now
            await session.commit()

    @staticmethod
    async def _persist_state(runtime: WorkflowRuntime) -> None:
        """把 worker 此刻的运行事实落到执行行（节点边界/挂起/终态，全部 await 串行）。

        挂起时把 runtime.awaiting_nodes 那份引擎上下文登记进 PauseState（生成审批
        凭据、记下结论落点与回写坐标）；状态列与两段式 variables 怎么写由
        ExecutionStateStore 定夺（docs/workflow-execution-contract.md §3.3）。
        """
        pause_state = None
        if runtime.status == STATUS_PAUSED:
            pause_state = PauseState()
            for context in runtime.awaiting_nodes.values():
                pause_state.register_from_engine_context(context)
        outputs = (runtime._collect_outputs() if runtime.status == STATUS_COMPLETED
                   else runtime.outputs)
        await ExecutionStateStore.save_run_result(runtime.execution_id, RunResultFacts(
            status=runtime.status,
            submit_mode=runtime.submit_mode,
            node_states=runtime.node_states_dict(),
            outputs=outputs,
            error_message=runtime.error,
            input_tokens=runtime.input_tokens,
            output_tokens=runtime.output_tokens,
            llm_call_count=runtime.llm_call_count,
            duration_ms=runtime.duration_ms,
            current_node_id=(runtime.ctx.executed[-1] if runtime.ctx.executed else None),
            awaiting_node_ids=([item.node_id for item in pause_state.pending]
                               if pause_state is not None else []),
            pause_state=pause_state,
        ))

    # ==================== 嵌套调用（【工作流】节点的子执行） ====================

    @staticmethod
    async def run_child(*, workflow_id: int, version: int, inputs: dict, user_id: int,
                        parent_exec_id: str, parent_node_id: str,
                        approver_identity: Optional[Any] = None) -> dict:
        """【工作流】节点：在本进程内跑一次子工作流执行，返回子行的执行结果。

        与 submit 的差别只在「不投队列」：父节点正 await 在这里，投出去就没人
        等它了。图取指定版本的发布快照并做同样严格的校验；执行本体复用 run_in_worker
        —— 子执行的节点明细/事件/暂停落库与一条普通执行完全一致（同一套状态机）。

        approver_identity 是父流自己那份「审批入参」（子流开始入参里的 APPROVER 字段）：
        子流那道审批的身份校验只认子执行这行 inputs，父流不往下传就是「父侧一提交就
        无审批权限」（补齐规则见 `seed_approver_identity`）。

        trigger_type 用 CHILD 标记「谁发起的」（该列无枚举约束、也不参与取图判定，
        详情接口不外发），存量数据与页面筛选都不受影响。
        """
        from service.service_workflow.services.workflow_service import WorkflowService
        graph_raw = await WorkflowService.get_snapshot(workflow_id, version)
        if not graph_raw:
            raise ValueError(f"子工作流 v{version} 的发布快照不存在，请先发布")
        graph = WorkflowGraph(graph_raw)
        errors = [i for i in graph.validate() if i.severity == "ERROR"]
        if errors:
            raise WorkflowGraphError(errors)
        inputs = seed_approver_identity(graph, dict(inputs or {}), approver_identity)

        execution_id = str(uuid.uuid4())
        await ExecutionStateStore.create_for_run(execution_id, NewRun(
            workflow_id=workflow_id, workflow_version=version, trigger_type="CHILD",
            graph_hash=compute_graph_hash(graph_raw), inputs=dict(inputs or {}),
            round_request=RoundRequest(round_no=1), user_id=user_id,
            parent_exec_id=parent_exec_id, parent_node_id=parent_node_id))
        await WorkflowExecutionService.run_in_worker(execution_id=execution_id)
        return await WorkflowExecutionService.get_child_result(execution_id)

    @staticmethod
    async def get_child_result(child_execution_id: str) -> dict:
        """读子执行结果行（【工作流】节点首跑与恢复轮共用同一份取数口径）。

        连 workflow_id/inputs 一起外发：LLM 节点的 tool-call 循环在恢复轮要凭这两个字段
        认出「上轮挂起的那份子执行属于哪个工作流工具、当时传了什么入参」，才能把
        这一次调用合成回消息历史里继续跑后面的轮次（而不是把子流程重跑一遍）。

        outputs 走 child_result_outputs：除了 END 声明的输出，还并上各节点对外可见的输出。

        PAUSED 时额外带一份 approval（子流程那道审批的真实上下文）：父侧只有拿到
        可编辑数据与两个提交坐标，才能在父流页面上画出表单让人直接审。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, child_execution_id)
            if row is None:
                raise ValueError(f"子执行记录不存在: {child_execution_id}")
            status = row.status
            node_states = dict(row.node_states or {})
            # 取出所需字段后再离开 session：session 关闭后 ORM 属性不可读
            awaiting = row.awaiting_node_id
            result = {"executionId": row.id, "status": status,
                      "workflowId": row.workflow_id, "inputs": row.inputs or {},
                      "outputs": child_result_outputs(row), "approval": None,
                      "errorMessage": row.error_message}
        if status == STATUS_PAUSED:
            pause = await ExecutionStateStore.read_pause_state(child_execution_id)
            result["approval"] = execution_tree.bubble_up(
                result["executionId"], pause, node_states, awaiting)
        return result

    @staticmethod
    async def _wake_parent(child_execution_id: str) -> None:
        """子执行收尾后驱动那条等它的父执行：该不该唤醒由 execution_tree 判，这里只管让它跑起来。

        预览执行没有 worker 在等队列，本进程直接续跑（事件沿用当前那条 WS，否则
        预览页会一直停在「等待审批」）；其余投队列，投不进去就把父行退回 PAUSED。
        """
        waiting = await wake_parent_execution(child_execution_id)
        if waiting is None:
            return
        if waiting.trigger_type == "DEBUG":
            await WorkflowExecutionService.run_in_worker(
                execution_id=waiting.execution_id, websocket=ws_in_scope())
            return
        try:
            await WorkflowExecutionService._drive(waiting.execution_id, None, 3)
        except ValueError:
            await ExecutionStateStore.rollback_claim(
                waiting.execution_id, STATUS_PAUSED, "唤醒父执行的投递失败，请重新提交")
            log.error("resume parent exec={} enqueue failed, rolled back to PAUSED",
                      waiting.execution_id)

    # ==================== 查询 ====================

    @staticmethod
    def _runtime_to_resp(runtime: WorkflowRuntime, outputs: dict,
                         error: Optional[str] = None) -> dict:
        return {
            "id": runtime.execution_id,
            "executionId": runtime.execution_id,
            "workflowId": str(runtime.workflow_id),
            "workflowVersion": runtime.workflow_version,
            "status": runtime.status,
            "inputs": runtime.ctx.inputs,
            "outputs": outputs,
            "nodeStates": runtime.node_states_dict(),
            "errorMessage": error or runtime.error,
            "startTime": None,
            "endTime": None,
            "duration": runtime.duration_ms,
            "inputTokens": runtime.input_tokens,
            "outputTokens": runtime.output_tokens,
            "totalTokens": runtime.input_tokens + runtime.output_tokens,
            "llmCallCount": runtime.llm_call_count,
            "executedNodes": runtime.ctx.executed,
            "currentNodeId": runtime.ctx.executed[-1] if runtime.ctx.executed else None,
            "userId": str(runtime.user_id),
            "createdTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    async def _row_to_resp(session, row: WorkflowExecution) -> dict:
        wf_name = None
        wf = await session.get(Workflow, row.workflow_id)
        if wf:
            wf_name = wf.name
        # 节点自定义名称/类型回填：新记录由引擎快照写入 node_states；
        # 历史记录缺失时从工作流图定义补齐（id→label/type）
        node_states = row.node_states or {}
        if isinstance(node_states, dict):
            label_map = {}
            for n in ((wf.graph if wf else None) or {}).get("nodes") or []:
                nid = str(n.get("id", ""))
                if nid:
                    label_map[nid] = (n.get("label") or n.get("name") or nid,
                                      n.get("type"))
            for nid, st in node_states.items():
                if not isinstance(st, dict):
                    continue
                meta = label_map.get(str(nid))
                if meta:
                    if not st.get("label"):
                        st["label"] = meta[0]
                    if not st.get("nodeType"):
                        st["nodeType"] = meta[1]
        duration = None
        if row.started_at and row.completed_at:
            duration = int((row.completed_at - row.started_at).total_seconds() * 1000)
        elif row.duration_ms:
            duration = row.duration_ms
        pause = ExecutionStateStore.pause_state_of_row(row)
        return {
            "id": row.id, "executionId": row.id,
            "workflowId": str(row.workflow_id), "workflowName": wf_name,
            "workflowVersion": row.workflow_version,
            "status": row.status,
            "submitMode": row.submit_mode,
            "graphHash": row.graph_hash,
            "pauseGeneration": pause.pause_generation,
            "pendingApprovals": approval_projection.public_approvals(pause),
            "inputs": row.inputs, "outputs": row.outputs,
            "nodeStates": node_states,
            "errorMessage": row.error_message,
            "startTime": row.started_at.strftime("%Y-%m-%d %H:%M:%S") if row.started_at else None,
            "endTime": row.completed_at.strftime("%Y-%m-%d %H:%M:%S") if row.completed_at else None,
            "duration": duration,
            "inputTokens": row.input_tokens, "outputTokens": row.output_tokens,
            "totalTokens": row.input_tokens + row.output_tokens,
            "llmCallCount": row.llm_call_count,
            "executedNodes": list(node_states.keys()),
            "currentNodeId": row.current_node_id,
            "userId": str(row.user_id),
            "createdTime": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else None,
        }

    @staticmethod
    async def get_execution(execution_id: str) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            return await WorkflowExecutionService._row_to_resp(session, row)

    @staticmethod
    async def page_executions(current: int = 1, size: int = 10, execution_id: str = None,
                              status: str = None, user_id: int = None):
        async with mysql_client.get_session() as session:
            stmt = select(WorkflowExecution)
            if execution_id:
                stmt = stmt.where(WorkflowExecution.id == execution_id)
            if status:
                stmt = stmt.where(WorkflowExecution.status == status)
            if user_id is not None:
                stmt = stmt.where(WorkflowExecution.user_id == user_id)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await session.execute(
                stmt.order_by(WorkflowExecution.create_time.desc())
                .offset((current - 1) * size).limit(size))).scalars().all()
            records = [await WorkflowExecutionService._row_to_resp(session, r) for r in rows]
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    async def node_executions(execution_id: str) -> list[dict]:
        """节点执行明细（调试面板数据源）。

        skip 与审批留痕从 node_states 里取（它们是跨轮权威数据），明细表不再
        单独存一列：两处存同一个事实必然分叉。
        """
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(WorkflowNodeExecution)
                .where(WorkflowNodeExecution.execution_id == execution_id)
                .order_by(WorkflowNodeExecution.node_order))).scalars().all()
            exec_row = await session.get(WorkflowExecution, execution_id)
            states = dict((exec_row.node_states if exec_row else None) or {})
            result = []
            for r in rows:
                st = states.get(r.node_id)
                st = st if isinstance(st, dict) else {}
                result.append({
                    "nodeId": r.node_id, "nodeType": r.node_type, "status": r.status,
                    "order": r.node_order, "input": r.input, "output": r.output,
                    "error": r.error, "duration": r.duration_ms,
                    # 本轮沿用（未重跑）标记：不带给面板就分不清「真跑了 0ms」还是「跳过了」
                    "skip": bool(st.get("skip")),
                    "branch": st.get("branch"),
                    "review": st.get("review"),
                    "reviewBy": st.get("reviewBy"),
                    "reviewOpinion": st.get("reviewOpinion"),
                    "reviewDiff": st.get("reviewDiff"),
                    "startedAt": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else None,
                    "completedAt": r.completed_at.strftime("%Y-%m-%d %H:%M:%S") if r.completed_at else None,
                })
            return result

    # ==================== SSE 订阅 ====================

    @staticmethod
    async def subscribe_events(execution_id: str):
        """SSE 事件流（FastAPI StreamingResponse 用）：本轮历史 + 实时帧接到终态。

        帧的读取、挂起帧该不该发、过期帧怎么丢，全在 event_frames；这里只把本进程
        的两个 IO 口递过去（读详情、订阅频道）。
        """
        async for item in event_frames.stream_frames(
                execution_id, read_detail=WorkflowExecutionService.get_execution,
                live_frames=subscribe_event_channel):
            yield item

    # ==================== 控制（DB 状态驱动，不经过队列信号） ====================

    @staticmethod
    async def _execution_status(execution_id: str) -> Optional[str]:
        """DB 中的执行状态（engine 每节点执行前经 status_check_hook 调用）。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            return row.status if row else None

    @staticmethod
    async def cancel(execution_id: str) -> bool:
        """取消：把执行行判成 CANCELLED（行不在 RUNNING/PAUSED 就取消不了）。

        engine 发现 CANCELLED 后走取消分支:结束在跑节点任务、
        持久化终态数据、广播 workflow.cancelled。
        """
        return await ExecutionStateStore.mark_cancelled(execution_id)

    @staticmethod
    async def check_api_key_scope(exec_id: Optional[str], workflow_id: Optional[int], api_key: str):
        """API Key 模式下的执行控制鉴权:key 有效,且其绑定的工作流 == 该执行所属工作流。

        submit / cancel 是「拿到 executionId 就能推进别人家的流程」的高危口,
        不能像 /subscribe 那样只靠 UUID 熵;key 与工作流的绑定关系就是边界。
        (上游网关只看路径形态,没有 DB 无法校归属)

        :raises ValueError: key 无效 / 执行不存在 / key 不属于该执行的工作流
        :return: 该执行所属 workflow_id
        """
        from service.service_workflow.services.workflow_apikey_service import (
            WorkflowApiKeyService,
        )
        cfg = await WorkflowApiKeyService.verify((api_key or "").strip())
        if not cfg:
            raise ValueError("无效的 API Key")
        if exec_id is not None:
            # 已有的执行实例的操作
            async with mysql_client.get_session() as session:
                row = await session.get(WorkflowExecution, exec_id)
                if row is None:
                    raise ValueError("执行记录不存在")
                workflow_id = row.workflow_id
            # 防越权
            if int(cfg.get("workflowId") or 0) != int(workflow_id):
                raise ValueError("API Key 无权操作该工作流的执行")
        else:
            # 新的执行
            if workflow_id is not None:
                if int(cfg.get("workflowId") or 0) != int(workflow_id):
                    raise ValueError("API Key 无权操作该工作流的执行")

    # ==================== 调试:快照 ====================
    @staticmethod
    async def get_snapshot(execution_id: str) -> Optional[dict]:
        """读这一行的两段跨轮状态（roundRequest + pauseState），只给排障与详情看。

        它不是恢复输入：续跑的权威源是 node_states，推进流程只有 submit 一条路，
        所以这个口只读。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            return row.variables or {}
