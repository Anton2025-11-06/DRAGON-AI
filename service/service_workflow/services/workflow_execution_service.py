# -*- coding: utf-8 -*-
"""工作流执行服务:执行(队列模式)/ 再提交(重新执行・审批恢复) / SSE 订阅 / 取消 / 查询。

执行模型(需求 4/5):
- execute_async:创建 tb_workflow_execution(RUNNING) → 投递 arq 队列任务 → 返回
  executionId;真正的执行在独立的 arq worker 进程(每进程自带事件循环);
  前端凭 executionId 走 subscribe SSE 接口(跨进程经 Redis Pub/Sub 实时订阅)。
- 取消由执行记录表状态驱动(需求 5):API 层改 DB 状态 → engine 每节点执行前
  status_check_hook 查 DB,读到 CANCELLED 即收尾。
- 暂停只有一个来源:审批节点拿不到结论时在节点边界挂起,run() 收尾置 PAUSED。
  恢复与重跑全部走「指定 executionId 再提交」接口(submit),两种模式都从 START
  遍历:RETRY 全量重跑,CONTINUE 命中上轮 COMPLETED 的节点走 skip。
- 节点明细/执行状态通过 runtime 钩子实时落库(node_persist_hook / state_persist_hook),
  全部 await 串行:火忘的迟到写会把上一轮数据盖到新一轮上。

已废弃(勿再引入):外部触发暂停 pause / resume / resume-from-snapshot /
PUT /variables/{name} / 断点。设计冻结见 docs/workflow-approval-memory.md。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from functools import partial
from typing import Optional

from sqlalchemy import delete, func, select, update
from fastapi import WebSocket

from common.common_arq.queue import enqueue_job, next_split_number
from common.common_exception.custom_exception import WorkflowGraphError
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.models.workflow_entity import (
    Workflow, WorkflowExecution, WorkflowNodeExecution,
)
from service.service_workflow.schemas.workflow_schema import WorkflowSubmitReq
from service.service_workflow.services.event_pubsub import (
    publish_event_hook, subscribe_event_channel,
)
from service.service_workflow.workflow_engine.engine import (
    STATUS_AWAITING, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
    STATUS_PAUSED, STATUS_PENDING, STATUS_RUNNING, STATUS_TIMEOUT,
    SUBMIT_MODE_CONTINUE, SUBMIT_MODE_RETRY, TERMINAL_STATUSES, WorkflowRuntime,
    compute_graph_hash,
)
from service.service_workflow.workflow_engine.events import EventBus
from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.model_client import (
    ModelConfigProvider,
)
from service.service_workflow.workflow_engine.nodes.approval_nodes import (
    verify_approver,
)
from common.common_httpx.httpx import httpx_pool


# 投递选片(按切片数轮询分发):切片数配置在 arq Redis 的 workflow_queue:split_number
# (不存在默认 1,由 system 监控页修改),按 execution_id 的 crc32 取模选切片队列
# (workflow_queue:split_{N});同一执行稳定落同一切片,见 common_arq.queue.next_split_number。


def get_shared_http():
    return httpx_pool.client


# 生产环境模型配置源(Redis 缓存 + MySQL 回源);测试环境可替换
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

    # ==================== 执行入口(API 进程:准备 + 投递) ====================

    @staticmethod
    async def execute_async(workflow_id: int, req, user_id: int = 0,
                            trigger_type: str = "API") -> str:
        """异步执行(需求 4:统一队列模式,不再有同步 execute)。

        流程:创建执行记录(RUNNING)→ 投递 arq 任务(job_id=execution_id 防重复)→
        返回 execution_id;前端凭 execution_id 调 subscribe SSE 接口订阅执行事件。
        """
        execution_id = await WorkflowExecutionService._prepare_execution(
            workflow_id, req, user_id, trigger_type)
        ok = await enqueue_job(
            "arq_tasks.tasks.workflow.execute_workflow",
            [execution_id],
            job_id=execution_id,
            split_number=await next_split_number(execution_id),
        )
        if not ok:
            # 理论上不会发生(job_id 为新建 UUID);兜底把记录标记失败,避免悬挂
            async with mysql_client.get_session() as session:
                row = await session.get(WorkflowExecution, execution_id)
                if row:
                    row.status = STATUS_FAILED
                    row.error_message = "任务投递失败"
                    await session.commit()
            raise Exception("任务投递失败")
        return execution_id

    @staticmethod
    async def execute_sync(websocket: WebSocket, workflow_id: int, req, user_id: int = 0,
                           trigger_type: str = "DEBUG"):
        """异步执行(需求 4:统一队列模式,不再有同步 execute)。

        流程:创建执行记录(RUNNING)→ 投递 arq 任务(job_id=execution_id 防重复)→
        返回 execution_id;前端凭 execution_id 调 subscribe SSE 接口订阅执行事件。
        """
        execution_id = await WorkflowExecutionService._prepare_execution(
            workflow_id, req, user_id, trigger_type)
        await WorkflowExecutionService.run_in_worker(execution_id=execution_id, websocket=websocket)

    @staticmethod
    async def _prepare_execution(workflow_id: int, req, user_id: int,
                                 trigger_type: str) -> str:
        """执行前置准备(API 进程):取图校验 → 创建 RUNNING 执行记录。

        :return: 新建的 execution_id(真正的执行由 arq worker 消费任务时重建运行时)
        """
        # 1. 取图:DEBUG → 草稿;API/AGENT → current_version 快照(未发布拒绝,需求:先发布再执行)
        async with mysql_client.get_session() as session:
            wf = await session.get(Workflow, workflow_id)
            if wf is None:
                raise ValueError("工作流不存在")
            graph_raw = wf.graph
            version = 0
            if trigger_type != "DEBUG":
                if wf.current_version <= 0:
                    raise ValueError("工作流尚未发布，请先发布后再执行")
                from service.service_workflow.services.workflow_service import WorkflowService
                snapshot = await WorkflowService.get_snapshot(workflow_id, wf.current_version)
                if snapshot:
                    graph_raw = snapshot
                    version = wf.current_version
            if not graph_raw:
                raise ValueError("工作流图为空(DEBUG 模式请先保存画布;正式调用请先发布)")

        # 2. 图解析 + 严格校验(提前失败,避免无效任务占队列)
        graph = WorkflowGraph(graph_raw)
        errors = [i for i in graph.validate() if i.severity == "ERROR"]
        if errors:
            raise WorkflowGraphError(errors)

        # 3. 创建执行记录(状态 RUNNING,权威状态在 DB,取消改这里;暂停由审批节点驱动)
        execution_id = str(uuid.uuid4())
        now = datetime.now()
        async with mysql_client.get_session() as session:
            row = WorkflowExecution(
                id=execution_id, workflow_id=workflow_id, workflow_version=version,
                status=STATUS_RUNNING, trigger_type=trigger_type, inputs=dict(req.inputs or {}),
                graph_hash=compute_graph_hash(graph_raw),
                started_at=now, user_id=user_id)
            session.add(row)
            await session.commit()
        return execution_id

    # ==================== 执行入口(arq worker 进程:重建运行时 + 驱动) ====================

    @staticmethod
    async def run_in_worker(execution_id: str, websocket: Optional[WebSocket] = None) -> None:
        """执行工作流(execute_workflow 任务入口,运行在 arq worker 进程)。

        首次执行与「指定 executionId 再提交」共用本入口与同一个 arq 任务函数,
        差异全在 DB 行里(submit_mode / node_states / variables.approvalDecisions)。

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

    @staticmethod
    async def _load_graph(session, row: WorkflowExecution) -> Optional[dict]:
        """取该执行记录**当时**使用的那份图。

        再提交必须用当时那份:草稿图若已被改过,上轮 node_states 里的 node_id 与
        新图对不上,skip/续跑就成了随机行为(画布漂移另有 graph_hash 校验兜底)。
        - workflow_version > 0(正式发布执行)→ 发布版本快照;
        - DEBUG / 未发布 → 草稿图。
        """
        if row.workflow_version > 0:
            from service.service_workflow.services.workflow_service import WorkflowService
            snap = await WorkflowService.get_snapshot(row.workflow_id, row.workflow_version)
            if snap:
                return snap
        wf = await session.get(Workflow, row.workflow_id)
        return wf.graph if wf else None

    @staticmethod
    async def _build_runtime(execution_id: str,
                             websocket: Optional[WebSocket] = None) -> Optional[WorkflowRuntime]:
        """从 DB 执行记录重建运行时(arq worker 进程内调用)。

        恢复的权威源是 tb_workflow_execution.node_states;variables 里的快照只在
        node_states 为空(老记录)时兜底。优先级不能反:快照里存着上一轮的 status 与
        审批结论,拿它盖掉本轮刚提交的 node_states 会把用户这次填的结论丢掉。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            graph_raw = await WorkflowExecutionService._load_graph(session, row)
            if not graph_raw:
                return None
            graph = WorkflowGraph(graph_raw)
            # 取出所需字段后再离开 session:session 关闭后 ORM 属性不可读
            snap = dict(row.variables or {})
            node_states = row.node_states or {}
            submit_mode = row.submit_mode or SUBMIT_MODE_RETRY
            inputs = dict(row.inputs or {})
            workflow_id, workflow_version = row.workflow_id, row.workflow_version
            trigger_type, user_id = row.trigger_type, row.user_id
            usage = (int(row.input_tokens or 0), int(row.output_tokens or 0),
                     int(row.llm_call_count or 0))
            duration_ms = int(row.duration_ms or 0)

        runtime = WorkflowRuntime(
            graph=graph, execution_id=execution_id,
            # 输入以执行记录行为准(再提交接口已把本轮 inputs 写回该行)
            inputs=inputs,
            model_provider=get_model_provider(),
            http_client=get_shared_http(),
            # 事件总线:
            # API执行：节点事件(含 node.delta)经 pub hook 实时 PUBLISH 到 Redis 频道，供其他进程的 SSE 订阅者跨进程实时消费
            # DEBUG 执行：节点事件(含 node.delta)经 websocket 传输
            event_bus=EventBus(execution_id, publish_hook=(partial(publish_event_hook, websocket=websocket)
                                                           if websocket is not None else publish_event_hook)),
            trigger_type=trigger_type,
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            submit_mode=submit_mode,
            # 当前对话轮次（提交接口已写好本轮值；首次执行 snap 为空 → 1）
            round_no=int(snap.get("round") or 1),
            # 本轮携带的审批结论:提交接口写进 variables,worker 消费即失效(不回灌)
            approval_decisions=snap.get("approvalDecisions") or {},
            node_persist_hook=WorkflowExecutionService._persist_node,
            state_persist_hook=WorkflowExecutionService._persist_state,
            status_check_hook=WorkflowExecutionService._execution_status,
        )
        if node_states:
            # RETRY 全量重跑:不重放全局变量(带着上轮循环计数会让 LOOP 首轮就退出)
            runtime.hydrate(node_states, replay_global=(submit_mode == SUBMIT_MODE_CONTINUE))
        elif snap:
            # 兼容改造前的老记录:只有旧格式快照(含 context/global)时走 restore
            runtime.restore(snap)
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
    async def _persist_state(runtime: WorkflowRuntime, paused: bool = False) -> None:
        """执行状态落库（节点边界/暂停/终态时调用，全部 await 串行）。

        variables 只存一份 runtime.snapshot()：早期同时写 global/context/snapshot
        三份副本，跨轮恢复时三份口径会分叉（node_states 才是权威源）。
        快照只在暂停与终态写：每个节点边界都刷一份大 JSON 既没必要又拖慢引擎。
        """
        outputs = runtime._collect_outputs() if runtime.status == STATUS_COMPLETED else runtime.outputs
        write_snapshot = paused or runtime.status in (
            STATUS_PAUSED, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, runtime.execution_id)
            if row is None:
                return
            row.status = runtime.status
            row.outputs = outputs
            row.node_states = runtime.node_states_dict()
            row.error_message = runtime.error
            row.input_tokens = runtime.input_tokens
            row.output_tokens = runtime.output_tokens
            row.llm_call_count = runtime.llm_call_count
            row.duration_ms = runtime.duration_ms
            row.current_node_id = runtime.ctx.executed[-1] if runtime.ctx.executed else None
            row.submit_mode = runtime.submit_mode
            # 等待审批的节点（供列表/详情直接判断「该不该弹审批表单」，免解 JSON）
            awaiting = [nid for nid, s in runtime.node_states.items()
                        if s.status == STATUS_AWAITING]
            row.awaiting_node_id = awaiting[0] if awaiting else None
            if write_snapshot:
                row.variables = runtime.snapshot()
            if runtime.status in TERMINAL_STATUSES:
                row.completed_at = datetime.now()
            await session.commit()

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
        snap = row.variables or {}
        # 审批上下文从快照取（快照只在暂停/终态写，PAUSED 行必然有）
        approval_context = snap.get("approvalContext") or {}
        awaiting_ids = (snap.get("awaitingNodeIds")
                        or WorkflowExecutionService._awaiting_ids(node_states, row.awaiting_node_id))
        return {
            "id": row.id, "executionId": row.id,
            "workflowId": str(row.workflow_id), "workflowName": wf_name,
            "workflowVersion": row.workflow_version,
            "status": row.status,
            "submitMode": row.submit_mode,
            "graphHash": row.graph_hash,
            "awaitingNodeIds": awaiting_ids,
            "approvalContext": approval_context,
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
    def _sse(event_type: str, payload: dict) -> str:
        data = json.dumps({"type": event_type, **payload},
                          ensure_ascii=False, default=str)
        return f"event: {event_type}\ndata: {data}\n\n"

    @staticmethod
    async def subscribe_events(execution_id: str):
        """SSE 事件流（FastAPI StreamingResponse 用）。
        执行运行在 arq worker 进程，本进程没有 runtime → 直接 SUBSCRIBE Redis
        事件频道实时消费（Pub/Sub 无历史，晚订阅已发生的事件由 DB 状态兜底）；
        已暂停/已结束 → 回放 DB nodeStates。

        PAUSED 不挂实时订阅：审批挂起时 worker 任务已结束，频道上不会再有事件，
        挂上去只会把连接死等到超时；审批后的新事件由下一次提交重新订阅。
        """
        status = await WorkflowExecutionService._execution_status(execution_id)
        if status == STATUS_RUNNING:
            async for frame in subscribe_event_channel(execution_id):
                yield frame
            return
        # 已结束/已暂停：从 DB 构造回放事件
        resp = await WorkflowExecutionService.get_execution(execution_id)
        if resp is None:
            yield WorkflowExecutionService._sse(
                "workflow.failed", {"executionId": execution_id, "error": "执行不存在"})
            return
        yield WorkflowExecutionService._sse(
            "workflow.started", {"executionId": execution_id, "inputs": resp.get("inputs")})
        for nid, st in (resp.get("nodeStates") or {}).items():
            # input 与实时流对齐：node.started 才带输入视图，回放漏掉的话
            # 已结束的执行在前端「执行过程」里就没有输入可展开
            yield WorkflowExecutionService._sse(
                "node.started", {"executionId": execution_id, "nodeId": nid,
                                 "nodeType": st.get("nodeType", ""),
                                 "input": st.get("input")})
            # 非成功终态的节点：跟实时流一致补发 node.timeout / node.cancelled /
            # node.paused（当成 failed 会误标红，当成 completed 会误标绿）
            node_status = st.get("status")
            if node_status == STATUS_AWAITING:
                yield WorkflowExecutionService._sse(
                    "node.paused", {"executionId": execution_id, "nodeId": nid,
                                    "nodeType": st.get("nodeType", ""),
                                    "duration": st.get("duration", 0),
                                    "approvalContext": (resp.get("approvalContext") or {}).get(nid)})
            elif node_status == STATUS_TIMEOUT:
                yield WorkflowExecutionService._sse(
                    "node.timeout", {"executionId": execution_id, "nodeId": nid,
                                     "duration": st.get("duration", 0),
                                     "error": st.get("error") or "并行分支等待超时"})
            elif node_status == STATUS_CANCELLED:
                yield WorkflowExecutionService._sse(
                    "node.cancelled", {"executionId": execution_id, "nodeId": nid,
                                       "duration": st.get("duration", 0),
                                       "error": st.get("error") or "分支已取消（并行短路或审批不同意）"})
            elif st.get("error"):
                yield WorkflowExecutionService._sse(
                    "node.failed", {"executionId": execution_id, "nodeId": nid,
                                    "error": st["error"]})
            else:
                # 恢复提交里被跳过的已完成节点：回放也要带 skip，否则前端
                # 会把它当成本轮真跑过的节点（耗时会假显示为 0ms 的正常完成）
                yield WorkflowExecutionService._sse(
                    "node.completed", {"executionId": execution_id, "nodeId": nid,
                                       "output": st.get("output"),
                                       "duration": st.get("duration", 0),
                                       "skip": bool(st.get("skip"))})
        final = resp["status"]
        if final == STATUS_PAUSED:
            awaiting = resp.get("awaitingNodeIds") or []
            nid = awaiting[0] if awaiting else (resp.get("currentNodeId") or "")
            yield WorkflowExecutionService._sse(
                "workflow.paused", {"executionId": execution_id, "nodeId": nid,
                                    "awaitingNodeIds": awaiting,
                                    "approvalContext": (resp.get("approvalContext") or {}).get(nid),
                                    "duration": resp.get("duration") or 0})
            return
        final_type = "workflow.completed" if final == STATUS_COMPLETED else (
            "workflow.cancelled" if final == STATUS_CANCELLED else "workflow.failed")
        payload = {"executionId": execution_id,
                   "outputs": resp.get("outputs"), "duration": resp.get("duration") or 0}
        if final == STATUS_FAILED:
            payload["error"] = resp.get("errorMessage") or "failed"
        yield WorkflowExecutionService._sse(final_type, payload)

    # ==================== 控制(需求 5:DB 状态驱动,不经过队列信号) ====================

    @staticmethod
    async def _execution_status(execution_id: str) -> Optional[str]:
        """DB 中的执行状态(engine 每节点执行前经 status_check_hook 调用)。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            return row.status if row else None

    # ==================== 再提交(需求 3:指定 executionId 的重新执行 / 审批恢复) ====================

    @staticmethod
    def _awaiting_ids(node_states: dict, awaiting_node_id: Optional[str]) -> list:
        """仍等待人工审批的节点：node_states 里的 AWAITING ∪ 行上 awaiting_node_id。

        两个来源都要：awaiting_node_id 是执行级快字段（列表页免解 JSON），
        node_states 才是引擎每一轮提交的完整集合（并行下可能多个审批节点同时挂起）。
        """
        ids = [nid for nid, s in (node_states or {}).items()
               if isinstance(s, dict) and s.get("status") == STATUS_AWAITING]
        if awaiting_node_id and awaiting_node_id not in ids:
            ids.append(awaiting_node_id)
        return ids

    @staticmethod
    def _retry_states(node_states: dict) -> dict:
        """RETRY（重新执行）的 node_states 清理：只清“本轮跑过什么”，保留记忆。

        - status 一律回 PENDING：skip 判据只认 COMPLETED，不清掉就一轮也不会跑；
        - llmMessages 原样保留（大模型记忆跨轮不丢，需求 1 决策 ①）；
        - review* 清除：重新执行后审批要从头拿结论，上轮结论留在页面上会被
          当成「本轮已审」；output 保留供详情展示（重跑会被新值覆写）；
        - branch 清除：重跑不沿用上轮走的出边端口（全量重跑下没有节点会被 skip，
          但详情页会拿它渲染「分支 x」标签，留着就是未跑节点的假路由）；
        - skip/duration/error 是本轮属性，必须归零。
        """
        cleaned = {}
        for nid, s in (node_states or {}).items():
            if not isinstance(s, dict):
                continue
            new = dict(s)
            new["status"] = STATUS_PENDING
            new["duration"] = 0
            new["error"] = None
            new["skip"] = False
            new["branch"] = None
            new["review"] = None
            new["reviewBy"] = None
            new["reviewOpinion"] = None
            new["reviewDiff"] = None
            cleaned[nid] = new
        return cleaned

    @staticmethod
    async def _prepare_submit(execution_id: str, req: WorkflowSubmitReq,
                              user_id: int) -> dict:
        """提交前校验(状态 × 模式 × 画布指纹 × 审批人权限)。

        :return: 投递上下文 {mode, prevStatus, graphHash, inputs, nodeStates, decisions}
        :raises ValueError: 任一条不满足(文案直接面向调用方,透传到 400)
        """
        mode = (req.mode or "").strip().upper()
        if mode not in (SUBMIT_MODE_RETRY, SUBMIT_MODE_CONTINUE):
            raise ValueError(f"提交模式非法：{req.mode}，仅支持 RETRY / CONTINUE")
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                raise ValueError("执行记录不存在")
            graph_raw = await WorkflowExecutionService._load_graph(session, row)
            prev_status, prev_hash = row.status, row.graph_hash
            node_states = dict(row.node_states or {})
            awaiting_col = row.awaiting_node_id
            row_inputs = dict(row.inputs or {})
        if prev_status in (STATUS_RUNNING, STATUS_PENDING):
            raise ValueError("上次任务未结束，请结束后再提交，或取消任务")
        if not graph_raw:
            raise ValueError("工作流图为空，无法提交")
        # 画布漂移:拓扑(节点/连线)变了就不能再按上轮状态跑,skip 与路由都会错位
        graph_hash = compute_graph_hash(graph_raw)
        if prev_hash and prev_hash != graph_hash:
            raise ValueError("画布已变更（节点或连线与上次执行不一致），请重新执行工作流")
        awaiting_ids = WorkflowExecutionService._awaiting_ids(node_states, awaiting_col)
        if mode == SUBMIT_MODE_CONTINUE:
            if prev_status != STATUS_PAUSED:
                raise ValueError(
                    f"暂停后恢复仅适用于 PAUSED 执行，当前状态 {prev_status}；如需重跑请用 RETRY")
            if not awaiting_ids:
                raise ValueError("该执行已无待审批节点，无法恢复，请改用 RETRY 重新执行")
            if not prev_hash:
                raise ValueError("历史执行缺少画布指纹，无法安全恢复，请改用 RETRY 重新执行")
        inputs = dict(req.inputs) if req.inputs is not None else row_inputs
        decisions: dict = {}
        if mode == SUBMIT_MODE_CONTINUE:
            graph = WorkflowGraph(graph_raw)
            items = list(req.approval or [])
            if not items:
                raise ValueError("暂停后恢复必须提交审批结论(approval)")
            known = set(awaiting_ids)
            if len(items) == 1 and not items[0].nodeId and len(known) == 1:
                items[0].nodeId = next(iter(known))  # 只有一个待审批节点时允许省 nodeId
            given = {d.nodeId for d in items}
            extra = given - known
            if extra:
                raise ValueError(f"审批结论包含非待审批节点：{sorted(extra)}")
            missing = known - given
            if missing:
                raise ValueError(f"仍有待审批节点未给出结论：{sorted(missing)}")
            allowed, review_by = verify_approver(graph, inputs)
            if not allowed:
                raise ValueError("无审批权限：请在输入参数中提供与画布审批人配置的审批入参值")
            for d in items:
                decisions[d.nodeId] = {
                    "approved": bool(d.approved),
                    "opinion": d.opinion or "",
                    # API Key 模式无登录态(user_id=0),留痕到调用形态而不是写个 0
                    "reviewBy": review_by or (str(user_id) if user_id else "API-KEY"),
                    "edits": [e.model_dump() for e in (d.edits or [])],
                }
        else:
            if awaiting_ids:
                # 带着未处理的审批选重跑:不拒绝(RETRY 本来就要跑掉该节点),但要留痕
                log.warning("submit RETRY with {} pending approval(s) exec={}",
                            len(awaiting_ids), execution_id)
        return {"mode": mode, "prevStatus": prev_status, "graphHash": graph_hash,
                "inputs": inputs, "nodeStates": node_states, "decisions": decisions}

    @staticmethod
    async def _apply_submit(execution_id: str, req: WorkflowSubmitReq,
                            user_id: int = 0) -> dict:
        """CAS 抢占行状态并一次性写入本轮数据(同步/异步提交共用)。

        互斥靠一条带 status 条件的 UPDATE:同一 executionId 同时点两次提交,只有
        一个能把行从「非 RUNNING」改成 RUNNING,另一个 rowcount=0 直接报错——
        用户要求「同一个任务,非终态的只能存在一个」在这落地。
        """
        ctx = await WorkflowExecutionService._prepare_submit(execution_id, req, user_id)
        mode = ctx["mode"]
        async with mysql_client.get_session() as session:
            upd = await session.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id,
                       WorkflowExecution.status.notin_([STATUS_RUNNING, STATUS_PENDING]))
                .values(status=STATUS_RUNNING, submit_mode=mode,
                        awaiting_node_id=None, graph_hash=ctx["graphHash"]))
            if upd.rowcount != 1:
                # 抢输了:事务里什么都没写,直接回失败文案(不靠 session 回滚兼底)
                await session.rollback()
                raise ValueError("他人正在提交，请勿重复提交相同任务")
            row = await session.get(WorkflowExecution, execution_id)
            # 轮次：RETRY 是新一次对话（+1，记忆因此跨轮累积）；CONTINUE 是同一轮
            # 被审批阻断后的续跑，不能另起一轮（否则同一轮的上下文被归为两段）
            prev_round = int((row.variables or {}).get("round") or 1)
            new_round = prev_round + 1 if mode == SUBMIT_MODE_RETRY else prev_round
            node_states = ctx["nodeStates"]
            if mode == SUBMIT_MODE_RETRY:
                node_states = WorkflowExecutionService._retry_states(node_states)
                # 重新执行:统计与耗时归零(本轮从头计数),不能累加上轮的 token
                row.input_tokens = row.output_tokens = row.llm_call_count = 0
                row.duration_ms = 0
                row.started_at = datetime.now()
            else:
                # 审批节点本轮按结论重新执行:清 AWAITING 标记,否则下轮又被当待审批
                for nid in ctx["decisions"]:
                    st = node_states.get(nid)
                    if isinstance(st, dict):
                        st["status"] = STATUS_PENDING
                        st["skip"] = False
            row.node_states = node_states
            row.inputs = ctx["inputs"]
            row.outputs = None
            row.error_message = None
            row.completed_at = None
            row.current_node_id = None
            # 本轮待消费状态:worker 从这里取审批结论与起始节点状态(跑完后会被快照覆写)
            row.variables = {"nodeStates": node_states,
                             "approvalDecisions": ctx["decisions"],
                             "submitMode": mode,
                             "round": new_round}
            # 明细表按轮重建:不删就会留下上一轮的节点行(与本轮 skip/重跑行混在一起)
            await session.execute(delete(WorkflowNodeExecution)
                                  .where(WorkflowNodeExecution.execution_id == execution_id))
            await session.commit()
        return ctx

    @staticmethod
    async def _rollback_submit(execution_id: str, prev_status: str) -> None:
        """投递失败回滚:行状态退回提交前。

        不回滚的话这条执行会永久卡在 RUNNING:既不能再提交(被「未结束」拦住),
        worker 也不会跑它(状态守卫直接 skip),只能手工改库。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_RUNNING:
                return
            row.status = prev_status
            await session.commit()
        log.warning("submit rolled back exec={} -> {}", execution_id, prev_status)

    @staticmethod
    async def submit(execution_id: str, req: WorkflowSubmitReq, user_id: int = 0,
                     retry_times: int = 3) -> dict:
        """指定 executionId 再提交(异步):校验 → CAS 抢占 → 投递 arq 任务。

        投递用 job_id=execution_id 与首跑同一个任务函数:arq 的 job_key 在任务结束时
        即释放(keep_result=0),所以同一执行可以多次投递;上一轮任务还在收尾时会投
        失败,故重试几次(用户口径:实在不行就等上一轮彻底结束)。
        """
        ctx = await WorkflowExecutionService._apply_submit(execution_id, req, user_id)
        enqueued = False
        for _ in range(max(1, retry_times)):
            if await enqueue_job(
                    "arq_tasks.tasks.workflow.execute_workflow", [execution_id],
                    job_id=execution_id,
                    split_number=await next_split_number(execution_id)):
                enqueued = True
                break
            await asyncio.sleep(1)
        if not enqueued:
            await WorkflowExecutionService._rollback_submit(execution_id, ctx["prevStatus"])
            raise ValueError("暂时无法重新提交，任务尚未彻底结束，请稍后再试")
        return {"id": execution_id, "executionId": execution_id,
                "status": STATUS_RUNNING, "mode": ctx["mode"]}

    @staticmethod
    async def submit_sync(websocket: WebSocket, execution_id: str,
                          req: WorkflowSubmitReq, user_id: int = 0) -> None:
        """指定 executionId 再提交(同步 WebSocket):校验/写库与异步一致,执行在本进程。

        预览运行(execute_sync)走的就是这条链路,再提交复用同一套状态机,
        保证「对话框里恢复审批」和「API 恢复审批」面对同一份 node_states。
        """
        await WorkflowExecutionService._apply_submit(execution_id, req, user_id)
        await WorkflowExecutionService.run_in_worker(execution_id=execution_id, websocket=websocket)

    @staticmethod
    async def cancel(execution_id: str) -> bool:
        """取消(需求 5):把执行记录状态改为 CANCELLED。

        engine 发现 CANCELLED 后走取消分支:结束在跑节点任务、
        持久化终态数据、广播 workflow.cancelled。

        PAUSED 也能取消:暂停态没有 worker 在跑(任务已正常结束),没人会再改这行状态,
        所以取消必须直接写终态并补 completed_at——否则它会停在 PAUSED 永远可提交。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status not in (STATUS_RUNNING, STATUS_PAUSED):
                return False
            row.status = STATUS_CANCELLED
            row.awaiting_node_id = None
            # RUNNING 时这一列会被引擎收尾时覆写一次;PAUSED 时没人覆写,必须在这补
            row.completed_at = datetime.now()
            await session.commit()
        return True

    @staticmethod
    async def check_api_key_scope(execution_id: str, api_key: str) -> int:
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
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                raise ValueError("执行记录不存在")
            workflow_id = row.workflow_id
        if int(cfg.get("workflowId") or 0) != int(workflow_id):
            raise ValueError("API Key 无权操作该工作流的执行")
        return workflow_id

    # ==================== 调试:快照 ====================

    @staticmethod
    async def get_snapshot(execution_id: str) -> Optional[dict]:
        """获取执行快照(仅暂停/终态落库,排障与详情展示用)。

        已废弃「拖拽快照改写后恢复」的调试玩法:快照不再是恢复输入(权威源是
        node_states,恢复入口只有 submit),这里降为只读。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            return row.variables or {}
