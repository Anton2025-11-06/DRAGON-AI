# -*- coding: utf-8 -*-
"""事件帧的读取与过期判定：把「本轮已跑出的」和「频道上正在发的」接成一条流。

读：执行行（详情形状经调用方注入的读口取）与 Redis 事件频道。本模块不写任何状态。
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Awaitable, Callable, Optional

from service.service_workflow.execution.execution_state import (
    ExecutionStateStore, RUNNING_STATUSES,
)
from service.service_workflow.workflow_engine.engine import (
    STATUS_AWAITING, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_PAUSED,
    STATUS_TIMEOUT,
)

# 本轮已停下、只等被推起时的那段订阅上限（秒）：子执行一进终态，收尾钩子就把父推起，
# 正常是秒级；到点仍没人推进（子执行被取消 / 钩子失败 / worker 挂了）也必须让订阅方
# 拿到一帧收尾，不能无限等一条不会再有事件的执行。
PARENT_RESUME_WINDOW_S = 300.0

# 两个读口由 service 注入：本模块不认识 MySQL 与 Redis。
DetailReader = Callable[[str], Awaitable[Optional[dict]]]
ChannelReader = Callable[..., AsyncIterator[str]]


def frame(event_type: str, payload: dict, pause_generation: int = 0) -> str:
    """拼一帧 SSE 文本，并盖上这一轮的挂起代次（消费方凭它丢过期帧）。"""
    data = {"type": event_type, **payload}
    if pause_generation:
        data["pauseGeneration"] = int(pause_generation)
    return (f"event: {event_type}\ndata: "
            f"{json.dumps(data, ensure_ascii=False, default=str)}\n\n")


def frame_generation(frame_text: str) -> int:
    """这一帧带的代次；不带这个键的帧（这套机制之前发出的）给 0。"""
    for line in frame_text.split("\n"):
        if line.startswith("data: "):
            try:
                return int(json.loads(line[len("data: "):]).get("pauseGeneration") or 0)
            except (ValueError, TypeError, json.JSONDecodeError):
                return 0
    return 0


def is_expired(frame_text: str, current_generation: int) -> bool:
    """是不是上一轮迟到的帧：代次低于本流认的代次即过期。

    新一轮开跑时代次先 +1，所以旧轮的帧只会小不会大；没带代次的帧一律不丢，否则
    worker 未重启的存量进程会把整条流滤空。
    """
    stamp = frame_generation(frame_text)
    return 0 < stamp < current_generation


def paused_frame(execution_id: str, resp: dict) -> str:
    """拼 workflow.paused：本轮已跑完、停在等人。欠着谁不在帧里，去详情看。"""
    return frame("workflow.paused", {"executionId": execution_id,
                                     "duration": resp.get("duration") or 0},
                 _generation_of(resp))


async def replay_frames(execution_id: str, resp: dict) -> AsyncIterator[str]:
    """把这一行已跑出的部分补成事件流（Pub/Sub 无历史，晚订阅者靠这个补看）。

    补出来的帧与实时发出的同形状：停在审批上的节点补 node.paused、本轮停下了就
    补 workflow.paused，晚来一步也能把面板画全（本轮被 skip 的上轮节点照样带 skip）。
    """
    generation = _generation_of(resp)
    yield frame("workflow.started",
                {"executionId": execution_id, "inputs": resp.get("inputs")}, generation)
    for nid, st in (resp.get("nodeStates") or {}).items():
        # input 与实时流对齐：node.started 才带输入视图，回放漏掉的话，已结束的执行在
        # 前端「执行过程」里就没有输入可展开
        yield frame("node.started", {"executionId": execution_id, "nodeId": nid,
                                     "nodeType": st.get("nodeType", ""),
                                     "input": st.get("input")}, generation)
        node_status = st.get("status")
        if node_status == STATUS_AWAITING:
            yield frame("node.paused",
                        {"executionId": execution_id, "nodeId": nid,
                         "nodeType": st.get("nodeType", ""),
                         "duration": st.get("duration", 0)}, generation)
        elif node_status == STATUS_TIMEOUT:
            yield frame("node.timeout", {"executionId": execution_id, "nodeId": nid,
                                         "duration": st.get("duration", 0),
                                         "error": st.get("error") or "并行分支等待超时"},
                        generation)
        elif node_status == STATUS_CANCELLED:
            yield frame("node.cancelled",
                        {"executionId": execution_id, "nodeId": nid,
                         "duration": st.get("duration", 0),
                         "error": st.get("error")
                                or "分支已取消（并行短路或审批不同意）"}, generation)
        elif st.get("error"):
            yield frame("node.failed", {"executionId": execution_id, "nodeId": nid,
                                        "error": st["error"]}, generation)
        else:
            # 恢复提交里被跳过的已完成节点：回放也要带 skip，否则前端会把它当成本轮
            # 真跑过的节点（耗时会假显示为 0ms 的正常完成）
            yield frame("node.completed", {"executionId": execution_id, "nodeId": nid,
                                           "output": st.get("output"),
                                           "duration": st.get("duration", 0),
                                           "skip": bool(st.get("skip"))}, generation)
    final = resp["status"]
    if final == STATUS_PAUSED:
        yield paused_frame(execution_id, resp)
        return
    payload = {"executionId": execution_id, "outputs": resp.get("outputs"),
               "duration": resp.get("duration") or 0}
    if final != STATUS_COMPLETED and final != STATUS_CANCELLED:
        payload["error"] = resp.get("errorMessage") or "failed"
    yield frame(_final_type(final), payload, generation)


async def stream_frames(execution_id: str, *, read_detail: DetailReader,
                        live_frames: ChannelReader) -> AsyncIterator[str]:
    """给出这条执行的完整事件流：先补本轮已跑出的历史，再续实时帧，终态或到点即结束。

    :param read_detail: 读这一行的对外详情（service 的 get_execution）
    :param live_frames: 订阅事件频道（service 的 subscribe_event_channel）

    分流只看两份事实：行状态、本轮是不是只欠一次唤醒（§2.5 的 C 链路）。本轮已停下的
    时候才接频道等它被推起；「还在等人答」与「已收尾」都不接——worker 那一轮已经结束，
    挂上去只会把连接死等到超时。过期判定只有一条：代次低于本次订阅起点即丢。
    """
    watch = await ExecutionStateStore.read_watch_state(execution_id)
    if not watch.exists:
        yield frame("workflow.failed", {"executionId": execution_id,
                                        "error": "执行不存在"})
        return
    awaiting_resume = watch.awaiting_resume
    if watch.status not in RUNNING_STATUSES:
        # 先补历史再接频道：顺序反了会把频道上已有的事件插进历史中间，页面就会先看
        # 到续跑、再看到上轮。补历史与 SUBSCRIBE 之间仍有一个丢事件窗口（Pub/Sub 无
        # 历史），靠前端看门狗继续探终态兜底。
        resp = await read_detail(execution_id)
        if resp is None:
            return
        async for item in replay_frames(execution_id, resp):
            yield item
        if not awaiting_resume:
            return
    async for item in live_frames(
            execution_id, max_wait_s=PARENT_RESUME_WINDOW_S if awaiting_resume else None):
        if not is_expired(item, watch.pause_generation):
            yield item


def _final_type(status: str) -> str:
    return {STATUS_COMPLETED: "workflow.completed",
            STATUS_CANCELLED: "workflow.cancelled"}.get(status, "workflow.failed")


def _generation_of(resp: dict) -> int:
    return int(resp.get("pauseGeneration") or 1)
