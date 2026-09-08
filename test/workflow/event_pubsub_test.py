# -*- coding: utf-8 -*-
"""跨进程事件通道测试：Redis Pub/Sub（替代原 cross_worker Stream 轮询桥）。

Part A（纯逻辑，不依赖 Redis/MySQL）：频道名约定、JSON 序列化往返、
node.delta 事件直通（无桥过滤）、SSE 帧格式、终态事件集合。

Part B（Redis 集成，前置 127.0.0.1:6379 可用，否则 SKIP）：
- 执行 worker 视角：WorkflowRuntime + 注入 pub hook 的 EventBus（事件实时
  PUBLISH 到频道 workflow:evt:{execution_id}）；
- API 进程视角：不注册 runtime，直接 subscribe_event_channel 实时订阅，
  验证跨进程能实时收到 workflow.started / node.* / workflow.completed，
  且 node.delta 同样直达（用户明确要求：所有数据包括 delta 都直接 pub）；
- 控制流（需求 5）：fake_db 模拟执行记录表，验证 DB 状态驱动的暂停/恢复/取消。
"""
import asyncio
import sys
import time

sys.path.insert(0, "E:/project/DRAGON-AI")

from service.service_workflow.services.event_pubsub import (
    TERMINAL_EVENT_TYPES, _event_from_message, channel_for,
    publish_event_hook, subscribe_event_channel,
)
from service.service_workflow.workflow_engine.events import EventBus, WorkflowEvent
from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.engine import WorkflowRuntime

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL") + f" | {name}" + (f" | {str(detail)[:260]}" if not ok else ""),
          flush=True)


def node(nid, ntype, label, x, y, data):
    return {"id": nid, "type": ntype, "label": label,
            "position": {"x": x, "y": y}, "data": data}


def edge(eid, src, tgt, handle=None):
    e = {"id": eid, "source": src, "target": tgt}
    if handle:
        e["sourceHandle"] = handle
    return e


def xw_graph():
    """START → A(模板) → END(answer={{n_a.text}})"""
    return {
        "nodes": [
            node("n_start", "START", "开始", 100, 300, {
                "fields": [{"name": "query", "label": "问题", "type": "INPUT",
                            "required": True, "defaultValue": "q"}]}),
            node("n_a", "TEMPLATE", "A", 350, 300, {
                "template": "A:{{n_start.query}}", "engine": "SIMPLE",
                "outputVariable": "text"}),
            node("n_end", "END", "结束", 600, 300, {
                "outputs": [{"name": "answer", "value": "{{n_a.text}}"}]}),
        ],
        "edges": [edge("e1", "n_start", "n_a"), edge("e2", "n_a", "n_end")],
    }


async def wait_status(rt, statuses, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if rt.status in statuses:
            return rt.status
        await asyncio.sleep(0.2)
    return rt.status


async def redis_available() -> bool:
    from common.common_redis.redis import client as redis_client
    try:
        await redis_client.client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def make_runtime(execution_id, graph, status_hook, snap_hook):
    """模拟 arq worker 里的装配：注入带 pub hook 的事件总线。"""
    return WorkflowRuntime(
        graph, execution_id, inputs={"query": "q"},
        # 与 workflow_execution_service._build_runtime 相同的注入方式
        event_bus=EventBus(execution_id, publish_hook=publish_event_hook),
        status_check_hook=status_hook,
        state_persist_hook=snap_hook,
    )


async def main():
    from common.common_redis.redis import client as redis_client

    # ==================== Part A：纯逻辑（无外部依赖） ====================
    check("U1 频道名 = workflow:evt:{execution_id}",
          channel_for("exec-1") == "workflow:evt:exec-1", channel_for("exec-1"))

    ev = WorkflowEvent("node.completed", "exec-2",
                       payload={"nodeId": "n_a", "output": {"text": "中文"}})
    back = _event_from_message(ev.to_json())
    check("U2 to_json → _event_from_message 往返还原",
          back.type == "node.completed" and back.execution_id == "exec-2"
          and back.timestamp == ev.timestamp and back.payload == ev.payload,
          back.payload)

    back2 = _event_from_message(ev.to_json().encode("utf-8"))
    check("U3 bytes 消息(redis decode_responses=False)同样还原",
          back2.type == "node.completed" and back2.payload == ev.payload, back2.payload)

    delta = WorkflowEvent("node.delta", "exec-3",
                          payload={"nodeId": "n_llm", "token": "x"})
    back3 = _event_from_message(delta.to_json())
    check("U4 node.delta 事件序列化/还原直达（无桥过滤白名单）",
          back3.type == "node.delta" and back3.payload == delta.payload, back3.payload)

    frame = WorkflowEvent("workflow.completed", "exec-4",
                          payload={"outputs": {"answer": "ok"}}).to_sse_frame()
    check("U5 SSE 帧格式(event:/data:)前端可解析",
          "event: workflow.completed" in frame and '"answer": "ok"' in frame, frame)

    check("U6 终态事件集合（订阅结束判定）",
          TERMINAL_EVENT_TYPES == {"workflow.completed", "workflow.failed",
                                   "workflow.cancelled"}, TERMINAL_EVENT_TYPES)

    # ==================== Part B：Redis 集成 ====================
    if not await redis_available():
        print("SKIP | Redis 不可用，跳过跨进程 Pub/Sub 集成测试")
        return

    graph = WorkflowGraph(xw_graph())

    # ---------- B1: node.delta 直接 PUBLISH，订阅端实时可达 ----------
    sub = asyncio.create_task(collect_types(
        subscribe_event_channel("pub-delta-001"), {"node.delta"}))
    await asyncio.sleep(0.3)   # 确保 SUBSCRIBE 已建立（Pub/Sub 无历史）
    await publish_event_hook(WorkflowEvent(
        "node.delta", "pub-delta-001", payload={"nodeId": "n_llm", "token": "你好"}))
    got = await sub
    check("B1 node.delta 实时 PUBLISH → 订阅端收到", "node.delta" in got, got)

    # ---------- B2: 执行 worker 执行时，跨进程实时收到完整事件流 ----------
    # 执行 worker 视角（模拟 run_in_worker）：runtime + 注入 pub hook
    fake_db = {"pub-002": "RUNNING", "snap": None}

    async def status_hook(eid):
        """模拟 engine 的 status_check_hook（真实实现查 mysql）。"""
        return fake_db[eid]

    async def snap_hook(runtime, paused=False):
        """模拟 state_persist_hook（真实实现把快照写进执行记录 variables）。"""
        fake_db["snap"] = runtime.snapshot()

    rt = make_runtime("pub-002", graph, status_hook, snap_hook)

    # API 进程视角：不注册 runtime，直接订阅事件频道（跨进程通道）
    sub2 = asyncio.create_task(collect_types(
        subscribe_event_channel("pub-002"),
        {"workflow.started", "node.completed", "workflow.completed"}))
    await asyncio.sleep(0.3)
    await rt.run()
    got2 = await sub2
    check("B2 跨进程实时收到 started/节点/终态事件",
          {"workflow.started", "node.started", "node.completed",
           "workflow.completed"} <= set(got2), got2)

    # ---------- C1: 暂停（API 改 DB 状态 PAUSED → engine 节点前暂停） ----------
    fake_db["pub-003"] = "RUNNING"
    fake_db["snap"] = None
    rt3 = make_runtime("pub-003", graph, status_hook, snap_hook)
    task3 = asyncio.create_task(rt3.run())
    await asyncio.sleep(0.5)             # 让 workflow.started 先发布
    fake_db["pub-003"] = "PAUSED"        # API 层改状态（需求 5）
    st3 = await wait_status(rt3, ["PAUSED", "COMPLETED", "FAILED"], timeout=8)
    check("C1 DB 状态 PAUSED → engine 在节点前暂停", st3 == "PAUSED", st3)
    check("C1.1 暂停快照已落库(含 pendingNodes)",
          bool(fake_db["snap"]), str(fake_db["snap"])[:120])
    await asyncio.wait_for(task3, timeout=5)
    check("C1.2 暂停后 run() 正常结束(不再 park 等信号)",
          rt3.finished and rt3.status == "PAUSED", rt3.status)

    # ---------- C2: 恢复（API 改状态 RUNNING + 重建运行时 restore 快照） ----------
    fake_db["pub-003"] = "RUNNING"
    rt4 = make_runtime("pub-003", graph, status_hook, snap_hook)
    rt4.restore(fake_db["snap"] or {})
    task4 = asyncio.create_task(rt4.run())
    st4 = await wait_status(rt4, ["COMPLETED", "FAILED"], timeout=10)
    check("C2 resume 后从 pending 节点继续执行", st4 == "COMPLETED", st4)
    check("C2.1 最终输出正确(answer=A:q)",
          rt4.outputs.get("answer") == "A:q", rt4.outputs)
    await asyncio.wait_for(task4, timeout=5)

    # ---------- C3: 取消（API 改 DB 状态 CANCELLED → engine 取消收尾） ----------
    fake_db["pub-004"] = "RUNNING"
    rt5 = make_runtime("pub-004", graph, status_hook, snap_hook)
    task5 = asyncio.create_task(rt5.run())
    await asyncio.sleep(0.5)
    fake_db["pub-004"] = "CANCELLED"
    st5 = await wait_status(rt5, ["CANCELLED", "COMPLETED", "FAILED"], timeout=8)
    check("C3 DB 状态 CANCELLED → engine 取消结束", st5 == "CANCELLED", st5)
    await asyncio.wait_for(task5, timeout=5)


async def collect_types(gen, want, timeout=8):
    """消费事件帧生成器，收集事件类型直到命中 want 全部或超时。"""
    got = []
    import re

    async def _run():
        async for frame in gen:
            m = re.search(r"event: (\S+)", frame)
            if m:
                got.append(m.group(1))
                if set(want) <= set(got):
                    return

    try:
        await asyncio.wait_for(_run(), timeout)
    except asyncio.TimeoutError:
        pass
    return got


if __name__ == "__main__":
    asyncio.run(main())
    total = len(results)
    passed = sum(1 for _, o, _ in results if o)
    print(f"\n===== event_pubsub: {passed}/{total} PASS =====")
    for name, o, d in results:
        if not o:
            print(f"  FAIL -> {name}: {str(d)[:200]}")
    sys.exit(0 if passed == total else 1)