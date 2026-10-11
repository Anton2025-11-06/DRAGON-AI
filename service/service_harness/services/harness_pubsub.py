# -*- coding: utf-8 -*-
"""跨进程轮次事件通道：Redis Pub/Sub（频道 harness:evt:{turn_id}）。

与 service_workflow 的 event_pubsub 同一套模式（本模块是它的 harness 简化版）：
- 发布：arq worker 进程里智能体 reply_stream 的每个事件（text.delta /
  thinking.delta / tool.* / turn.*）实时 PUBLISH 到频道，无后台常驻任务；
- 订阅：API 进程的 SSE 订阅者 SUBSCRIBE 同一频道实时消费；
- Pub/Sub 无历史消息：晚订阅者靠 DB 兜底——轮次已终态时直接按
  tb_harness_message 的 ASSISTANT 行回放收尾帧；订阅中连续空读超时也查一次
  DB（worker 可能在订阅建立前就收完了尾）。

帧协议（SSE）：``data: {"type": ..., "turnId": ..., "ts": ..., ...payload}\\n\\n``，
终态集合 = {turn.completed, turn.failed, turn.interrupted}，前端收到即关流。
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator, Optional

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client as redis_client

from service.service_harness.models.harness_entity import (
    HarnessMessage, TURN_COMPLETED, TURN_FAILED, TURN_INTERRUPTED, TURN_RUNNING,
)

# ==================== 常量 ====================
EVT_CHANNEL_PREFIX = "harness:evt:"  # 事件频道前缀（每轮对话一个频道）
# 阻塞读窗口（秒）：redis-py get_message(timeout=...) 的单位就是秒
SUBSCRIBE_IDLE_S = 5.0
IDLE_DB_CHECK = 4  # 连续空读达到该次数后查 DB 收尾

# ==================== 事件类型（worker 发布侧与前端消费侧的唯一约定） ====================
EVT_TURN_STARTED = "turn.started"
EVT_TEXT_DELTA = "text.delta"
EVT_THINKING_DELTA = "thinking.delta"
EVT_TOOL_STARTED = "tool.started"
EVT_TOOL_COMPLETED = "tool.completed"
EVT_TOOL_RESULT_DELTA = "tool.result.delta"
EVT_TOOL_RESULT_COMPLETED = "tool.result.completed"
EVT_TURN_COMPLETED = "turn.completed"
EVT_TURN_FAILED = "turn.failed"
EVT_TURN_INTERRUPTED = "turn.interrupted"

TERMINAL_EVENT_TYPES = {EVT_TURN_COMPLETED, EVT_TURN_FAILED, EVT_TURN_INTERRUPTED}
TERMINAL_DB_STATUSES = {TURN_COMPLETED, TURN_FAILED, TURN_INTERRUPTED}


def channel_for(turn_id: str) -> str:
    """事件频道名：harness:evt:{turn_id}（发布端与订阅端唯一约定）。"""
    return f"{EVT_CHANNEL_PREFIX}{turn_id}"


def _event_dict(event_type: str, turn_id: str, payload: dict) -> dict:
    """拼一帧事件的完整字典（发布与回放共用，保证两边帧形状一致）。"""
    return {"type": event_type, "turnId": turn_id, "ts": int(time.time() * 1000),
            **payload}


def sse_frame(event_type: str, turn_id: str, payload: dict) -> str:
    """拼一帧 SSE 文本（订阅端 yield 给 StreamingResponse 的最终形态）。"""
    data = _event_dict(event_type, turn_id, payload)
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ==================== 发布（arq worker 进程） ====================

async def publish_turn_event(turn_id: str, event_type: str,
                             payload: Optional[dict] = None) -> None:
    """把一个事件实时投到频道（worker 每收到一个 agentscope 事件调一次）。

    失败只记日志不抛：worker 的第一职责是把对话跑完并落库，订阅方有 DB 兜底
    （终态一定收得到），中途丢几帧只是页面少几段增量。
    """
    try:
        body = json.dumps(_event_dict(event_type, turn_id, payload or {}),
                          ensure_ascii=False, default=str)
        await redis_client.client.publish(channel_for(turn_id), body)
    except Exception as e:  # noqa: BLE001
        log.warning("harness event publish failed turn={}: {}", turn_id, e)


# ==================== DB 兜底读口 ====================

async def _db_turn_row(turn_id: str) -> Optional[HarnessMessage]:
    """查这一轮的 ASSISTANT 行（状态权威行；turn_id 全局唯一）。"""
    from sqlalchemy import select

    async with mysql_client.get_session() as session:
        return (await session.execute(
            select(HarnessMessage).where(HarnessMessage.turn_id == turn_id,
                                         HarnessMessage.role == "ASSISTANT")
        )).scalar_one_or_none()


def _row_snapshot(row: HarnessMessage) -> dict:
    """在 session 关闭前把兜底需要的字段拷出来（ORM 属性离开 session 不可读）。"""
    return {"status": row.status, "content": row.content, "thinking": row.thinking,
            "tool_calls": row.tool_calls, "error": row.error}


async def _final_frame_from_snapshot(turn_id: str, snap: dict) -> Optional[str]:
    """DB 已是终态但频道无消息时的兜底终态帧（行还在 RUNNING 则 None）。"""
    if snap["status"] not in TERMINAL_DB_STATUSES:
        return None
    if snap["status"] == TURN_COMPLETED:
        return sse_frame(EVT_TURN_COMPLETED, turn_id,
                         {"content": snap["content"] or ""})
    if snap["status"] == TURN_INTERRUPTED:
        return sse_frame(EVT_TURN_INTERRUPTED, turn_id,
                         {"reason": snap["error"] or "本轮已中断"})
    return sse_frame(EVT_TURN_FAILED, turn_id, {"error": snap["error"] or "failed"})


# ==================== 订阅（API 进程，SSE 用） ====================

async def subscribe_turn_events(turn_id: str) -> AsyncIterator[str]:
    """订阅事件频道（跨进程 SSE 用）：SUBSCRIBE 后实时消费事件帧。

    结束条件：① 收到终态事件；② 连续空读超时且 DB 已终态（频道无消息可收，
    worker 早已收尾）。finally 必须释放专用 pubsub 连接，防止 SSE 长连接泄漏。
    """
    channel = channel_for(turn_id)
    ps = redis_client.client.pubsub()
    try:
        await ps.subscribe(channel)
        idle = 0
        while True:
            msg = await ps.get_message(ignore_subscribe_messages=True,
                                       timeout=SUBSCRIBE_IDLE_S)
            if msg is None:
                idle += 1
                # 空读兜底：轮次可能已结束（频道无消息可收）→ 查 DB 收尾
                if idle >= IDLE_DB_CHECK:
                    row = await _db_turn_row(turn_id)
                    snap = _row_snapshot(row) if row is not None else {"status": None}
                    frame = await _final_frame_from_snapshot(turn_id, snap)
                    if frame:
                        yield frame
                    if row is None or snap["status"] in TERMINAL_DB_STATUSES:
                        return
                    idle = 0
                continue
            if msg.get("type") != "message":
                continue
            raw = msg["data"]
            body = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            # 发布端发的是纯 JSON，这里补成 SSE 帧形态（与回放帧同一形状）
            yield f"data: {body}\n\n"
            try:
                ev_type = json.loads(body).get("type", "")
            except (ValueError, TypeError):
                continue
            if ev_type in TERMINAL_EVENT_TYPES:
                return
    finally:
        # 订阅者退出必须释放专用连接（与 event_pubsub 同款收尾）
        try:
            await ps.unsubscribe(channel)
        finally:
            await ps.aclose()


async def _replay_finished_turn(turn_id: str, snap: dict) -> AsyncIterator[str]:
    """把已终态的一轮补成事件流（晚订阅者补看；帧形状与实时流一致）。

    顺序按「思考 → 工具轨迹 → 正文 → 终态」还原：前端按 type 增量渲染，
    回放帧与实时帧走同一套拼接口径。
    """
    yield sse_frame(EVT_TURN_STARTED, turn_id, {})
    if snap["thinking"]:
        yield sse_frame(EVT_THINKING_DELTA, turn_id, {"content": snap["thinking"]})
    for call in (snap["tool_calls"] or []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or "")
        yield sse_frame(EVT_TOOL_STARTED, turn_id, {"tool": tool,
                                                    "toolCallId": call.get("toolCallId")})
        yield sse_frame(EVT_TOOL_COMPLETED, turn_id,
                        {"tool": tool, "toolCallId": call.get("toolCallId"),
                         "arguments": call.get("arguments")})
    if snap["content"]:
        yield sse_frame(EVT_TEXT_DELTA, turn_id, {"content": snap["content"]})
    frame = await _final_frame_from_snapshot(turn_id, snap)
    if frame:
        yield frame


async def stream_turn_frames(turn_id: str) -> AsyncIterator[str]:
    """给出这一轮的完整事件流（StreamingResponse 直接消费）。

    分流只看 ASSISTANT 行一个事实：
    - 已终态 → 回放（worker 那一轮已经结束，挂频道只会死等到超时）；
    - RUNNING → 实时订阅（worker 正在跑或即将跑，终态由频道或 DB 兜底送达）。
    """
    row = await _db_turn_row(turn_id)
    if row is None:
        yield sse_frame(EVT_TURN_FAILED, turn_id, {"error": "对话轮次不存在"})
        return
    snap = _row_snapshot(row)
    if snap["status"] in TERMINAL_DB_STATUSES:
        async for frame in _replay_finished_turn(turn_id, snap):
            yield frame
        return
    async for frame in subscribe_turn_events(turn_id):
        yield frame


# 说明：agentscope 事件 → 协议帧的转换（type/payload）在 worker 侧完成
# （arq_tasks/tasks/harness.py）：它本来就要累积 text/thinking/tool_calls 落库，
# 转换与累积在同一处才不会两套口径。本模块保持纯通道，不 import agentscope
# ——API 进程不装 agentscope 也能起服务、SSE 订阅也能回放已终态的轮次。
