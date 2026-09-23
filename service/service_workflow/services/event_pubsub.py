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

控制（取消与审批挂起/恢复）走 DB 状态与 submit，不走本通道。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator, Optional
from weakref import WeakKeyDictionary

from fastapi import WebSocket

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client as redis_client
from service.service_workflow.workflow_engine.engine import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
)
from service.service_workflow.workflow_engine.events import WorkflowEvent

# ---------- 常量 ----------
EVT_CHANNEL_PREFIX = "workflow:evt:"  # 事件频道前缀（每次执行一个频道）
# 阻塞读窗口用秒：redis-py 的 get_message(timeout=...) 单位就是秒。早先这里写的是
# 5000（毫秒常量直接塞进秒参数），空读兜底要 83 分钟才走得到一次，等于没有。
SUBSCRIBE_IDLE_S = 5.0  # 订阅阻塞读窗口（秒）
IDLE_DB_CHECK = 4  # 连续空读达到该次数后查 DB 收尾

TERMINAL_EVENT_TYPES = {"workflow.completed", "workflow.failed", "workflow.cancelled"}
FINAL_DB_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}


def channel_for(execution_id: str) -> str:
    """事件频道名：workflow:evt:{execution_id}（发布端与订阅端唯一约定）。"""
    return f"{EVT_CHANNEL_PREFIX}{execution_id}"


# ==================== DEBUG（预览运行）事件回推通道 ====================
WS_FRAME_TIMEOUT = 10  # 单帧发送上限（秒）：宁可丢帧，也不让发送方挂住
WS_QUEUE_SIZE = 2000  # 待发送帧缓冲上限
_WS_FLUSH = object()  # 队列收尾标记（pump 收到即退出）


class _WsEventSender:
    """一条 WS 连接一个串行事件发送器（缓冲 + 单任务发送 + 单帧超时）。

    为什么不能让引擎直接 await websocket.send_text：
    1. 事件来自多个并发任务（并行分支各自 emit），uvicorn 的 send 不是并发安全的，
       多路同时 send 会互踩；
    2. send 是背压操作：对端不读我就写不出去。预览链路是
       引擎 → API 进程 → 网关 → 浏览器，任一环节不读了（页面切后台/关掉/网关排队），
       这个 await 就永不返回；而它挂在引擎任务上 → _wait_running 收不了尾 →
       workflow.paused 永远不发、执行记录永久停在 RUNNING（既不能提交审批，
       也只能看着画布一直「执行中」）。
    改为「emit 侧只入队、由单个 pump 任务串行发、单帧带超时」：发送再慢/再坏也只
    丢事件，不会阻塞引擎推进。
    """

    def __init__(self, websocket: WebSocket):
        self._ws = websocket
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_SIZE)
        self._task: Optional[asyncio.Task] = None
        self._broken = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump())

    async def submit(self, frame: str) -> None:
        """入队一帧（不 await 传输）。缓冲满/连接已坏即丢弃并告警。"""
        if self._broken:
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._broken = True
            log.warning("ws event buffer full ({}), 后续事件不再回推", WS_QUEUE_SIZE)

    async def _pump(self) -> None:
        while True:
            frame = await self._queue.get()
            if frame is _WS_FLUSH:
                return
            try:
                await asyncio.wait_for(self._ws.send_text(frame),
                                       timeout=WS_FRAME_TIMEOUT)
            except asyncio.TimeoutError:
                self._broken = True
                log.warning("ws event send timeout >{}s, 本连接后续事件不再回推",
                            WS_FRAME_TIMEOUT)
                return
            except Exception as e:  # noqa: BLE001
                self._broken = True
                log.warning("ws event send failed: {}（本连接后续事件不再回推）", e)
                return

    async def stop(self) -> None:
        """收尾：把已入队的事件发完再退出，全程有上限（不能让端点卡在关闭上）。"""
        task, self._task = self._task, None
        if task is None:
            return
        if not self._broken:
            try:
                await asyncio.wait_for(self._queue.put(_WS_FLUSH),
                                       timeout=WS_FRAME_TIMEOUT)
            except asyncio.TimeoutError:
                pass
        try:
            await asyncio.wait_for(task, timeout=WS_FRAME_TIMEOUT + 5)
        except BaseException:  # noqa: BLE001 含 TimeoutError/CancelledError
            task.cancel()


_WS_SENDERS: "WeakKeyDictionary" = WeakKeyDictionary()

# 当前调用链上的事件回推连接（只在显式圈定的范围内生效，见 use_ws_event_scope）。
# 存在的理由只有一个：把子执行跑在这条连接上时，子执行终态钩子里推起的**父执行**
# 也得认同一条连接（run_in_worker(parent_id) 是钩子内部发起的，拿不到形参 ws），
# 否则父流程续跑的事件只能落到 Redis 频道，预览页/对话页会一直停在「等待审批」。
# 不做全局注入：run_child 在父的 WS 范围内跑子执行时，子执行的节点事件用的父图里
# 根本没有的 node_id，混进同一条连接只会把调试面板画乱。
_WS_SCOPE: ContextVar[Optional[WebSocket]] = ContextVar("workflow_event_ws", default=None)


def ws_in_scope() -> Optional[WebSocket]:
    """取当前范围内的事件回推连接（没有则 None，即照旧走 Redis 频道）。"""
    return _WS_SCOPE.get()


@contextmanager
def use_ws_event_scope(websocket: Optional[WebSocket]) -> Iterator[None]:
    """把 websocket 登记为当前调用链的事件出口（退出即还原，跨任务不污染）。"""
    token = _WS_SCOPE.set(websocket)
    try:
        yield
    finally:
        _WS_SCOPE.reset(token)


def start_ws_event_channel(websocket: WebSocket) -> None:
    """WS 端点 accept 后调用：为本连接建立串行事件发送通道。"""
    sender = _WsEventSender(websocket)
    sender.start()
    _WS_SENDERS[websocket] = sender


async def stop_ws_event_channel(websocket: WebSocket) -> None:
    """关闭连接前调用：冲刷并停掉 pump（必须在 send 收尾帧之前）。"""
    sender = _WS_SENDERS.pop(websocket, None)
    if sender is not None:
        await sender.stop()


async def publish_event_hook(event: WorkflowEvent,
                             websocket: Optional[WebSocket] = None) -> None:
    """EventBus 的 pub hook：把事件实时投递给消费方。

    由 service 层装配 runtime 时注入（EventBus(publish_hook=...)），
    节点执行过程中的所有事件（含 node.delta token 流）即时广播，
    供其他进程的 SSE 订阅者消费。失败只记日志，不阻断引擎本地执行。

    websocket 非空（DEBUG 同步执行）时经本连接的串行发送通道回推（只入队，
    不 await 传输），否则走 Redis 频道。
    """
    try:
        if websocket is not None:
            sender = _WS_SENDERS.get(websocket)
            if sender is not None:
                await sender.submit(event.to_json())
            else:
                # 未建通道（端点外的直驱调用）：兜底原地发送，异常照旧被下面捕获
                await asyncio.wait_for(websocket.send_text(event.to_json()),
                                       timeout=WS_FRAME_TIMEOUT)
        else:
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


async def subscribe_event_channel(execution_id: str,
                                  *, max_wait_s: Optional[float] = None) -> AsyncIterator[str]:
    """订阅事件频道（跨进程 SSE 用）：SUBSCRIBE 后实时消费事件帧。

    结束条件：① 收到终态事件；② 连续空读超时且 DB 已是终态（频道无消息）；
    ③ 给了 max_wait_s 则到点即止——**到点不发任何帧**，收尾由调用方按当时的
    DB 状态决定（调用方才知道「这次静默」是「没人接着跑」还是「还在等子执行」）。

    max_wait_s 是给「父等子」准备的：那段窗口里父行既不是 RUNNING（订阅方按状态
    分流会去回放历史）、频道上又确实没人在发事件，只能有界地等它被钩子推起。
    注意 Pub/Sub 无历史：晚订阅丢的事件由调用方用 DB 状态兜底。
    """
    channel = channel_for(execution_id)
    ps = redis_client.client.pubsub()
    loop = asyncio.get_running_loop()
    deadline = None if max_wait_s is None else loop.time() + max(0.0, float(max_wait_s))
    try:
        await ps.subscribe(channel)
        idle = 0
        while True:
            if deadline is not None and loop.time() >= deadline:
                return
            msg = await ps.get_message(ignore_subscribe_messages=True,
                                       timeout=SUBSCRIBE_IDLE_S)
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
