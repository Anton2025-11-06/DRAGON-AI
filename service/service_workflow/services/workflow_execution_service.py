# -*- coding: utf-8 -*-
"""工作流执行服务:执行(队列模式)/ SSE 订阅 / 控制(暂停/恢复/取消)/ 调试(变量/快照)。

执行模型(需求 4/5):
- execute_async:创建 tb_workflow_execution(RUNNING) → 投递 arq 队列任务 → 返回
  executionId;真正的执行在独立的 arq worker 进程(每进程自带事件循环);
  前端凭 executionId 走 subscribe SSE 接口(跨进程经 Redis Pub/Sub 实时订阅)。
- 控制(取消/暂停/恢复)不通过队列信号,由执行记录表状态驱动(需求 5):
  API 层改 DB 状态 → engine 每节点执行前 status_check_hook 查 DB 决定去留;
  resume 时 API 改状态 RUNNING + 合并 edit 变量 → 投递 resume 任务 → worker
  从 DB 快照重建运行时继续执行。
- 节点明细/执行状态通过 runtime 钩子实时落库(node_persist_hook / state_persist_hook)。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from common.common_arq.queue import enqueue_job, next_split_number
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.models.workflow_entity import (
    Workflow, WorkflowExecution, WorkflowNodeExecution,
)
from service.service_workflow.services.event_pubsub import (
    publish_event_hook, subscribe_event_channel,
)
from service.service_workflow.services.workflow_file_service import WorkflowFileService
from service.service_workflow.workflow_engine.engine import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_PAUSED,
    STATUS_RUNNING, WorkflowRuntime,
)
from service.service_workflow.workflow_engine.events import EventBus
from service.service_workflow.workflow_engine.graph import WorkflowGraph, WorkflowGraphError
from service.service_workflow.workflow_engine.model_client import (
    ModelConfigProvider,
)
from common.common_httpx.httpx import httpx_pool
import time


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
                            trigger_type: str = "DEBUG") -> str:
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

        # 3. 创建执行记录(状态 RUNNING,权威状态在 DB,取消/暂停/恢复都改这里)
        execution_id = str(uuid.uuid4())
        now = datetime.now()
        async with mysql_client.get_session() as session:
            row = WorkflowExecution(
                id=execution_id, workflow_id=workflow_id, workflow_version=version,
                status=STATUS_RUNNING, trigger_type=trigger_type, inputs=dict(req.inputs or {}),
                breakpoints=list(req.breakpoints or []), started_at=now, user_id=user_id)
            session.add(row)
            await session.commit()
        return execution_id

    # ==================== 执行入口(arq worker 进程:重建运行时 + 驱动) ====================

    @staticmethod
    async def run_in_worker(execution_id: str) -> None:
        """执行工作流(execute_workflow 任务入口,运行在 arq worker 进程)。

        从 DB 执行记录重建运行时并驱动引擎;执行中每节点前由
        status_check_hook 查 DB 状态(取消/暂停即时生效)。
        """
        runtime = await WorkflowExecutionService._build_runtime(execution_id)
        if runtime is None:
            log.warning("workflow run skipped: exec={} not found", execution_id)
            return
        try:
            await runtime.run()
        except Exception as e:  # noqa: BLE001
            log.error("workflow run failed exec={}: {}", execution_id, e)

    @staticmethod
    async def resume_in_worker(execution_id: str) -> None:
        """恢复暂停的执行(resume_workflow 任务入口,运行在 arq worker 进程)。

        状态守卫:只处理 DB 状态为 RUNNING 的记录——若投递后/消费前用户又取消了
        执行,此处必须拒绝恢复,避免「僵尸执行」继续跑(修复旧版 _resume_worker
        无状态守卫的缺陷)。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_RUNNING:
                log.warning("resume skipped: exec={} status!=", execution_id)
                return
        runtime = await WorkflowExecutionService._build_runtime(execution_id)
        if runtime is None:
            return
        try:
            await runtime.run()
        except Exception as e:  # noqa: BLE001
            log.error("workflow resume failed exec={}: {}", execution_id, e)

    @staticmethod
    async def _build_runtime(execution_id: str,
                             snapshot: Optional[dict] = None) -> Optional[WorkflowRuntime]:
        """从 DB 执行记录重建运行时(arq worker 进程内调用)。

        :param snapshot: 恢复用快照(优先级高于行内 variables 字段);
                        通常为 None,直接读暂停时落库的 variables。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            snap = snapshot if snapshot is not None else (row.variables or {})
            # 图:非 DEBUG(正式发布)优先用发布版本快照,否则用草稿图
            wf = await session.get(Workflow, row.workflow_id)
            if wf is None:
                return None
            graph_raw = wf.graph
            if row.trigger_type != "DEBUG" and row.workflow_version > 0:
                from service.service_workflow.services.workflow_service import WorkflowService
                graph_snap = await WorkflowService.get_snapshot(
                    row.workflow_id, row.workflow_version)
                if graph_snap:
                    graph_raw = graph_snap
            if not graph_raw:
                return None
            graph = WorkflowGraph(graph_raw)
            workflow_id = row.workflow_id
            workflow_version = row.workflow_version
            trigger_type = row.trigger_type
            user_id = row.user_id

        runtime = WorkflowRuntime(
            graph=graph, execution_id=execution_id,
            # 新触发执行:输入与断点直接取执行记录行(restore 会以快照覆盖
            # inputs/断点,因此只在存在快照时调用,避免把新执行输入清空)
            inputs=dict(row.inputs or {}),
            breakpoints=list(row.breakpoints or []),
            model_provider=get_model_provider(),
            http_client=get_shared_http(),
            # 事件总线:节点事件(含 node.delta)经 pub hook 实时 PUBLISH 到 Redis 频道,
            # 供其他进程的 SSE 订阅者跨进程实时消费
            event_bus=EventBus(execution_id, publish_hook=publish_event_hook),
            # DOC_EXTRACTOR 文件加载：fileId/本地路径 → (text, metadata)
            file_loader=WorkflowFileService.load_and_extract,
            trigger_type=trigger_type,
            user_id=user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            node_persist_hook=WorkflowExecutionService._persist_node,
            state_persist_hook=WorkflowExecutionService._persist_state,
            status_check_hook=WorkflowExecutionService._execution_status,
        )
        # 暂停恢复:快照(含 context/pendingNodes/剩余断点)权威高于行字段;
        # 新触发执行 variables 为空,跳过 restore 保留上面传入的真实输入
        if snap:
            runtime.restore(snap)
        return runtime

    # ==================== 持久化钩子（引擎回调） ====================

    @staticmethod
    async def _persist_node(runtime: WorkflowRuntime, node, state) -> None:
        start = time.monotonic()
        """节点状态变化落库（RUNNING→新增行，COMPLETED/FAILED→更新行）。"""
        async with mysql_client.get_session() as session:
            if state.status == STATUS_RUNNING:
                session.add(WorkflowNodeExecution(
                    execution_id=runtime.execution_id, node_id=node.id, node_type=node.type,
                    status=STATUS_RUNNING, node_order=state.order, input=state.input,
                    started_at=datetime.now()))
                await session.commit()
            else:
                existing = (await session.execute(
                    select(WorkflowNodeExecution)
                    .where(WorkflowNodeExecution.execution_id == runtime.execution_id,
                           WorkflowNodeExecution.node_id == node.id,
                           WorkflowNodeExecution.node_order == state.order)
                )).scalar_one_or_none()
                if existing:
                    existing.status = state.status
                    existing.output = state.output
                    existing.error = state.error
                    existing.duration_ms = state.duration
                    existing.completed_at = datetime.now()
                    await session.commit()
        end = time.monotonic()
        # loguru 只认 {} 占位符，%s 风格不会被替换（静默失效）
        log.info("node persist done took={}ms", int((end - start) * 1000))

    @staticmethod
    async def _persist_state(runtime: WorkflowRuntime, paused: bool = False) -> None:
        """执行状态落库（节点边界/结束/暂停时调用）。"""
        outputs = runtime._collect_outputs() if runtime.status == STATUS_COMPLETED else runtime.outputs
        variables = {"global": runtime.ctx.global_vars,
                     "context": runtime.ctx.to_dict(),
                     "snapshot": runtime.snapshot()}
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
            if paused:
                row.variables = variables
            if runtime.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
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
        duration = None
        if row.started_at and row.completed_at:
            duration = int((row.completed_at - row.started_at).total_seconds() * 1000)
        elif row.duration_ms:
            duration = row.duration_ms
        return {
            "id": row.id, "executionId": row.id,
            "workflowId": str(row.workflow_id), "workflowName": wf_name,
            "workflowVersion": row.workflow_version,
            "status": row.status,
            "inputs": row.inputs, "outputs": row.outputs,
            "nodeStates": row.node_states,
            "errorMessage": row.error_message,
            "startTime": row.started_at.strftime("%Y-%m-%d %H:%M:%S") if row.started_at else None,
            "endTime": row.completed_at.strftime("%Y-%m-%d %H:%M:%S") if row.completed_at else None,
            "duration": duration,
            "inputTokens": row.input_tokens, "outputTokens": row.output_tokens,
            "totalTokens": row.input_tokens + row.output_tokens,
            "llmCallCount": row.llm_call_count,
            "executedNodes": list((row.node_states or {}).keys()),
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
    async def page_executions(current: int = 1, size: int = 10, workflow_id: str = None,
                              status: str = None, user_id: int = None):
        async with mysql_client.get_session() as session:
            stmt = select(WorkflowExecution)
            if workflow_id:
                stmt = stmt.where(WorkflowExecution.workflow_id == int(workflow_id))
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
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(WorkflowNodeExecution)
                .where(WorkflowNodeExecution.execution_id == execution_id)
                .order_by(WorkflowNodeExecution.node_order))).scalars().all()
            return [{
                "nodeId": r.node_id, "nodeType": r.node_type, "status": r.status,
                "order": r.node_order, "input": r.input, "output": r.output,
                "error": r.error, "duration": r.duration_ms,
                "startedAt": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else None,
                "completedAt": r.completed_at.strftime("%Y-%m-%d %H:%M:%S") if r.completed_at else None,
            } for r in rows]

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
        已结束 → 回放 DB nodeStates。"""
        status = await WorkflowExecutionService._execution_status(execution_id)
        if status in (STATUS_RUNNING, STATUS_PAUSED):
            async for frame in subscribe_event_channel(execution_id):
                yield frame
            return
        # 已结束：从 DB 构造回放事件
        resp = await WorkflowExecutionService.get_execution(execution_id)
        if resp is None:
            yield WorkflowExecutionService._sse(
                "workflow.failed", {"executionId": execution_id, "error": "执行不存在"})
            return
        yield WorkflowExecutionService._sse(
            "workflow.started", {"executionId": execution_id, "inputs": resp.get("inputs")})
        for nid, st in (resp.get("nodeStates") or {}).items():
            yield WorkflowExecutionService._sse(
                "node.started", {"executionId": execution_id, "nodeId": nid,
                                 "nodeType": st.get("nodeType", "")})
            if st.get("error"):
                yield WorkflowExecutionService._sse(
                    "node.failed", {"executionId": execution_id, "nodeId": nid,
                                    "error": st["error"]})
            else:
                yield WorkflowExecutionService._sse(
                    "node.completed", {"executionId": execution_id, "nodeId": nid,
                                       "output": st.get("output"),
                                       "duration": st.get("duration", 0)})
        final_type = "workflow.completed" if resp["status"] == STATUS_COMPLETED else "workflow.failed"
        payload = {"executionId": execution_id,
                   "outputs": resp.get("outputs"), "duration": resp.get("duration") or 0}
        if resp["status"] == STATUS_FAILED:
            payload["error"] = resp.get("errorMessage") or "failed"
        yield WorkflowExecutionService._sse(final_type, payload)

    # ==================== 控制(需求 5:DB 状态驱动,不经过队列信号) ====================

    @staticmethod
    async def _execution_status(execution_id: str) -> Optional[str]:
        """DB 中的执行状态(engine 每节点执行前经 status_check_hook 调用)。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            return row.status if row else None

    @staticmethod
    async def pause(execution_id: str) -> bool:
        """暂停(需求 5):把执行记录状态改为 PAUSED。

        engine 在下个节点执行前的状态检查点发现 PAUSED 后,
        会落库暂停快照(含 variables)、广播 workflow.paused 并正常结束执行。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_RUNNING:
                return False
            row.status = STATUS_PAUSED
            await session.commit()
        return True

    @staticmethod
    async def resume(execution_id: str,
                     variables: Optional[dict] = None,
                     snapshot: Optional[dict] = None) -> bool:
        """恢复暂停的执行:改状态 RUNNING + 合并 edit 数据 → 投递 resume 任务。

        :param variables: 前端 edit 的全局变量(合并进 DB 快照的 global 段)
        :param snapshot: 完整快照(调试面板拖拽快照场景,整份替换 DB variables)
        - resume 任务消费后,worker 从 DB 快照重建运行时,从 pending 节点继续。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_PAUSED:
                return False
            if snapshot is not None:
                row.variables = snapshot
            elif variables:
                # 把 edit 变量合并进快照的 global 段(restore 时顶层 global 为权威)
                snap = row.variables or {}
                merged = dict(snap.get("global") or {})
                merged.update(variables)
                snap["global"] = merged
                row.variables = snap
            row.status = STATUS_RUNNING
            await session.commit()
        # 重新投递 resume 任务(job_id 全局唯一:重复点击 resume 不会重复消费)
        return await enqueue_job(
            "arq_tasks.tasks.workflow.resume_workflow",
            [execution_id],
            job_id=f"resume:{execution_id}",
            split_number=await next_split_number(execution_id),
        )

    @staticmethod
    async def cancel(execution_id: str) -> bool:
        """取消(需求 5):把执行记录状态改为 CANCELLED。

        engine 发现 CANCELLED 后走取消分支:结束在跑节点任务、
        持久化终态数据、广播 workflow.cancelled。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status not in (STATUS_RUNNING, STATUS_PAUSED):
                return False
            row.status = STATUS_CANCELLED
            await session.commit()
        return True

    # ==================== 调试:变量 / 快照 ====================

    @staticmethod
    async def update_variable(execution_id: str, name: str, value) -> bool:
        """调试:更新全局变量(仅暂停态可改)。

        执行在 arq worker 进程,运行中的变量在 worker 内存、API 无法直改;
        因此只在 PAUSED 时写入 DB 快照的 global 段,resume 任务 restore 后生效。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None or row.status != STATUS_PAUSED:
                return False
            snap = row.variables or {}
            merged = dict(snap.get("global") or {})
            merged[name] = value
            snap["global"] = merged
            row.variables = snap
            await session.commit()
        return True

    @staticmethod
    async def get_snapshot(execution_id: str) -> Optional[dict]:
        """获取执行快照(暂停时落库的 variables 即权威快照)。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowExecution, execution_id)
            if row is None:
                return None
            return row.variables or {}

    @staticmethod
    async def resume_from_snapshot(execution_id: str, snapshot: dict) -> dict:
        """从快照恢复执行(调试接口):整份快照写入 DB 后走标准 resume 流程。"""
        ok = await WorkflowExecutionService.resume(execution_id, snapshot=snapshot)
        if not ok:
            raise ValueError("执行不存在或未处于暂停状态")
        return {
            "id": execution_id,
            "executionId": execution_id,
            "status": STATUS_RUNNING,
        }
