# -*- coding: utf-8 -*-
"""节点「返回内容」开关测试（引擎直驱，不依赖 Redis/MySQL）。

需求：工作流编排中每个节点可设置返回内容开关——
- 开（默认）：该节点数据事件（node.completed 的 output、node.delta token 流）emit 给客户端；
- 关：上述数据事件不广播（node.started / node.failed / workflow 级事件不受影响）；
- 数据持久化一直开启，与开关无关（node_persist_hook 照常调用、state.output 照常写入）。
"""
import asyncio
import sys

sys.path.insert(0, "E:/project/DRAGON-AI")

from service.service_workflow.workflow_engine.engine import WorkflowRuntime
from service.service_workflow.workflow_engine.events import EventBus, WorkflowEvent
from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.nodes.base import BaseNodeExecutor

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


def xw_graph(extra_a_cfg=None):
    """START → A(模板) → END(answer={{n_a.text}})。extra_a_cfg 会合并进 A 节点配置。"""
    cfg_a = {"template": "A:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "text"}
    if extra_a_cfg:
        cfg_a = {**cfg_a, **extra_a_cfg}
    return {
        "nodes": [
            node("n_start", "START", "开始", 100, 300, {
                "fields": [{"name": "query", "label": "问题", "type": "INPUT",
                            "required": True, "defaultValue": "q"}]}),
            node("n_a", "TEMPLATE", "A", 350, 300, cfg_a),
            node("n_end", "END", "结束", 600, 300, {
                "outputs": [{"name": "answer", "value": "{{n_a.text}}"}]}),
        ],
        "edges": [edge("e1", "n_start", "n_a"), edge("e2", "n_a", "n_end")],
    }


def events_collector(events):
    """EventBus pub hook：把每次发布的事件收集到 list（本地，无需 Redis）。"""
    async def hook(event: WorkflowEvent) -> None:
        events.append(event)
    return hook


async def run_once(events, node_cfg_extra=None, with_persist=False, persist_calls=None):
    """构建图 + 运行时并跑完，返回 (runtime, events)。"""
    graph = WorkflowGraph(xw_graph(node_cfg_extra))
    runtime = WorkflowRuntime(
        graph, "emit-sw-001", inputs={"query": "q"},
        event_bus=EventBus("emit-sw-001", publish_hook=events_collector(events)),
        node_persist_hook=(lambda rt, nd, st: persist_calls.append(
            {"nodeId": nd.id, "status": st.status, "output": st.output})) if with_persist else None,
    )
    await runtime.run()
    return runtime


def completed_of(events, node_id):
    return [e for e in events if e.type == "node.completed" and e.payload.get("nodeId") == node_id]


def started_of(events, node_id):
    return [e for e in events if e.type == "node.started" and e.payload.get("nodeId") == node_id]


def main():
    async def amain():
        # ==================== U1 缺省（未配置开关）→ 正常广播 ====================
        events = []
        rt = await run_once(events)
        ok1 = len(completed_of(events, "n_a")) == 1 and len(completed_of(events, "n_start")) == 1
        check("U1 缺省开关:全部节点 node.completed 正常广播", ok1,
              [(e.type, e.payload.get("nodeId")) for e in events])
        check("U1.1 广播事件含节点输出数据",
              completed_of(events, "n_a")[0].payload.get("output", {}).get("text") == "A:q",
              completed_of(events, "n_a")[0].payload.get("output"))

        # ==================== U2 emitOutput=false → 数据事件不广播 ====================
        events = []
        rt2 = await run_once(events, {"emitOutput": False})
        n_a_completed = completed_of(events, "n_a")
        check("U2 开关关闭:该节点无 node.completed", len(n_a_completed) == 0, [e.type for e in events])
        check("U2.1 开关关闭:node.started 仍广播(进度,不含返回数据)",
              len(started_of(events, "n_a")) == 1, [e.type for e in events])
        check("U2.2 开关关闭:workflow.completed 仍广播(终态含 END 聚合输出)",
              any(e.type == "workflow.completed" for e in events), [e.type for e in events])
        check("U2.3 执行与内存输出不受影响", rt2.node_states["n_a"].output.get("text") == "A:q",
              rt2.node_states["n_a"].output)

        # ==================== U3 emitOutput=false → node.delta 不广播 ====================
        # 直驱 BaseNodeExecutor.emit_delta（模拟 LLM 流式 token）
        events = []
        graph3 = WorkflowGraph(xw_graph({"emitOutput": False}))
        rt3 = WorkflowRuntime(graph3, "emit-sw-003", inputs={"query": "q"},
                              event_bus=EventBus("emit-sw-003", publish_hook=events_collector(events)))
        ex3 = BaseNodeExecutor(graph3.get_node("n_a"), rt3)
        await ex3.emit_delta("你好")
        check("U3 开关关闭:node.delta 不广播", not any(e.type == "node.delta" for e in events),
              [e.type for e in events])

        events4 = []
        rt4 = WorkflowRuntime(WorkflowGraph(xw_graph()), "emit-sw-004", inputs={"query": "q"},
                              event_bus=EventBus("emit-sw-004", publish_hook=events_collector(events4)))
        ex4 = BaseNodeExecutor(rt4.graph.get_node("n_a"), rt4)
        await ex4.emit_delta("你好")
        check("U3.1 开关开启(缺省):node.delta 正常广播",
              any(e.type == "node.delta" and e.payload.get("token") == "你好" for e in events4),
              [e.type for e in events4])

        # ==================== U4 emitOutput=true 显式 → 正常广播 ====================
        events = []
        await run_once(events, {"emitOutput": True})
        check("U4 emitOutput=true 显式开启:node.completed 正常广播",
              len(completed_of(events, "n_a")) == 1, [e.type for e in events])

        # ==================== U5 持久化不受开关影响 ====================
        persist_calls = []
        events = []
        rt5 = WorkflowRuntime(
            WorkflowGraph(xw_graph({"emitOutput": False})), "emit-sw-005", inputs={"query": "q"},
            event_bus=EventBus("emit-sw-005", publish_hook=events_collector(events)),
            node_persist_hook=(lambda rt, nd, st: persist_calls.append(
                {"nodeId": nd.id, "status": st.status, "output": st.output})),
        )
        await rt5.run()
        pa = [c for c in persist_calls if c["nodeId"] == "n_a"]
        check("U5 开关关闭:node_persist_hook 照常落库(节点明细)",
              len(pa) == 2 and pa[0]["status"] == "RUNNING" and pa[1]["status"] == "COMPLETED",
              pa)
        check("U5.1 开关关闭:节点状态与输出仍写入 node_states",
              rt5.node_states["n_a"].status == "COMPLETED"
              and rt5.node_states["n_a"].output.get("text") == "A:q",
              rt5.node_states["n_a"].to_dict() if hasattr(rt5.node_states["n_a"], "to_dict") else "")

    asyncio.run(amain())
    total = len(results)
    passed = sum(1 for _, o, _ in results if o)
    print(f"\n===== emit_switch: {passed}/{total} PASS =====")
    for name, o, d in results:
        if not o:
            print(f"  FAIL -> {name}: {str(d)[:200]}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()