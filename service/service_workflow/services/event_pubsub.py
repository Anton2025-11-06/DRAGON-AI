# -*- coding: utf-8 -*-
"""跨进程事件通道：Redis Pub/Sub（替代原 Redis Stream 轮询桥）。

背景：工作流执行任务运行在独立的 arq worker 进程（自带 asyncio 事件循环），
API 进程（任意 uvicorn worker）需要把执行事件实时推送给用户。不采用
「后台轮询桥 + Stream」方案，改为真正的事件驱动：

- 发布：engine 每个节点执行时产生的事件（node.started / node.delta /
  node.completed / node.failed，以及 workflow.started / paused / resumed /
  completed / failed / cancelled），由 EventBus 注入的 pub hook
  （publish_event_hook）实时 PUBLISH 到 Redis 频道 workflow:evt:{execution_id}，
  无需任何后台常驻任务（node.delta 的 token 级流式同样实时到达）；
- 订阅：其他进程的 SSE 订阅者 SUBSCRIBE 同一频道实时消费。

Pub/Sub 的天然限制是没有历史消息：晚订阅者会丢事件，因此订阅端用 DB 兜底：
- 调用方（subscribe_events）先查执行记录：已终态 → 直接按 DB 回放终态帧；
- 订阅过程中连续空读超时 → 查 DB：已终态则补发终态帧收尾。

控制（取消/暂停/恢复）仍为 DB 状态控制（需求 5），不走本通道。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client as redis_client
from service.service_workflow.workflow_engine.engine import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
)
from service.service_workflow.workflow_engine.events import WorkflowEvent

# ---------- 常量 ----------
EVT_CHANNEL_PREFIX = "workflow:evt:"   # 事件频道前缀（每次执行一个频道）
SUBSCRIBE_IDLE_MS = 5000               # 订阅阻塞读窗口（毫秒）
IDLE_DB_CHECK = 4                      # 连续空读达到该次数后查 DB 收尾

TERMINAL_EVENT_TYPES = {"workflow.completed", "workflow.failed", "workflow.cancelled"}
FINAL_DB_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}


def channel_for(execution_id: str) -> str:
    """事件频道名：workflow:evt:{execution_id}（发布端与订阅端唯一约定）。"""
    return f"{EVT_CHANNEL_PREFIX}{execution_id}"


async def publish_event_hook(event: WorkflowEvent) -> None:
    """EventBus 的 pub hook：把事件实时 PUBLISH 到 Redis 频道。

    由 service 层装配 runtime 时注入（EventBus(publish_hook=...)），
    节点执行过程中的所有事件（含 node.delta token 流）即时广播，
    供其他进程的 SSE 订阅者消费。失败只记日志，不阻断引擎本地执行。
    """
    try:
        await redis_client.client.publish(channel_for(event.execution_id),
                                          event.to_json())
    except Exception as e:  # noqa: BLE001
        log.warning("event publish failed exec={}: {}", event.execution_id, e)


def _event_from_message(data: Any) -> WorkflowEvent:
    """把频道消息（bytes/str 形式的 JSON）还原为 WorkflowEvent。"""
    raw = data.decode("utf-8") if isinstance(data, bytes) else str(data)
    d = json.loads(raw)
    return WorkflowEvent(
        type=d.get("type", "unknown"),
        execution_id=d.get("executionId", ""),
        timestamp=d.get("timestamp", 0),
        payload={k: v for k, v in d.items()
                 if k not in ("type", "executionId", "timestamp")},
    )


async def _db_execution_row(execution_id: str):
    from service.service_workflow.models.workflow_entity import WorkflowExecution
    async with mysql_client.get_session() as session:
        return await session.get(WorkflowExecution, execution_id)


async def _final_frame_from_db(execution_id: str) -> Optional[str]:
    """DB 已是终态但频道无消息时的兜底终态帧。"""
    row = await _db_execution_row(execution_id)
    if row is None or row.status not in FINAL_DB_STATUSES:
        return None
    if row.status == STATUS_COMPLETED:
        ev = WorkflowEvent("workflow.completed", execution_id,
                           payload={"outputs": row.outputs,
                                    "duration": row.duration_ms or 0})
    else:
        ev = WorkflowEvent(
            "workflow.cancelled" if row.status == STATUS_CANCELLED else "workflow.failed",
            execution_id,
            payload={"error": row.error_message or row.status})
    return ev.to_sse_frame()


async def subscribe_event_channel(execution_id: str) -> AsyncIterator[str]:
    """订阅事件频道（跨进程 SSE 用）：SUBSCRIBE 后实时消费事件帧。

    结束条件：① 收到终态事件；② 连续空读超时且 DB 已是终态（频道无消息）。
    注意 Pub/Sub 无历史：晚订阅丢的事件由调用方用 DB 状态兜底。
    """
    channel = channel_for(execution_id)
    ps = redis_client.client.pubsub()
    try:
        await ps.subscribe(channel)
        idle = 0
        while True:
            msg = await ps.get_message(ignore_subscribe_messages=True,
                                       timeout=SUBSCRIBE_IDLE_MS)
            if msg is None:
                idle += 1
                # 空读兜底：执行可能已结束（频道无消息可收）→ 查 DB 收尾
                if idle >= IDLE_DB_CHECK:
                    row = await _db_execution_row(execution_id)
                    if row is None or row.status in FINAL_DB_STATUSES:
                        frame = await _final_frame_from_db(execution_id)
                        if frame:
                            yield frame
                        return
                    idle = 0
                continue
            if msg.get("type") != "message":
                continue
            ev = _event_from_message(msg["data"])
            yield ev.to_sse_frame()
            if ev.type in TERMINAL_EVENT_TYPES:
                return
    finally:
        # 订阅者退出必须释放专用连接，防止 SSE 长连接泄漏
        try:
            await ps.unsubscribe(channel)
        finally:
            await ps.aclose()