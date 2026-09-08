# -*- coding: utf-8 -*-
"""直接驱动引擎复现 PARALLEL 死锁（不经 HTTP），faulthandler 抓栈。"""
import asyncio
import faulthandler
import sys

sys.path.insert(0, "E:/project/DRAGON-AI")

from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.engine import WorkflowRuntime


def node(i, t, l, x, y, d):
    return {"id": i, "type": t, "label": l, "position": {"x": x, "y": y}, "data": d}


def edge(i, a, b, h=None):
    e = {"id": i, "source": a, "target": b}
    if h:
        e["sourceHandle"] = h
    return e


def build(which):
    common_start = node("n_start", "START", "开始", 100, 300, {
        "fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": "q"}]})
    common_end = node("n_end", "END", "结束", 900, 300, {"outputs": [{"name": "answer", "value": "{{n_par.branches}}"}]})
    if which == "parallel":
        return {
            "nodes": [common_start,
                      node("n_par", "PARALLEL", "并行", 350, 300, {"branches": [{"id": "p1", "name": "分支1"}, {"id": "p2", "name": "分支2"}], "waitStrategy": "ALL"}),
                      node("n_a", "TEMPLATE", "A", 600, 150, {"template": "分支A完成", "engine": "SIMPLE", "outputVariable": "text"}),
                      node("n_b", "TEMPLATE", "B", 600, 450, {"template": "分支B完成", "engine": "SIMPLE", "outputVariable": "text"}),
                      common_end],
            "edges": [edge("e1", "n_start", "n_par"),
                      edge("e2", "n_par", "n_a", "branch:p1"),
                      edge("e3", "n_par", "n_b", "branch:p2"),
                      edge("e4", "n_par", "n_end")]}
    if which == "http":
        return {
            "nodes": [common_start,
                      node("n_http", "HTTP_REQUEST", "HTTP", 400, 300, {"url": "http://127.0.0.1:18000/api/workflow/workflows/node-definitions", "method": "GET", "outputVariable": "response"}),
                      node("n_end2", "END", "结束", 900, 300, {"outputs": [{"name": "answer", "value": "{{n_http.response}}"}]})],
            "edges": [edge("e1", "n_start", "n_http"), edge("e2", "n_http", "n_end2")]}


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "parallel"
    graph = WorkflowGraph(build(which))
    rt = WorkflowRuntime(graph, "repro-001", inputs={"query": "q"})

    async def watchdog():
        await asyncio.sleep(6)
        print("\n===== TASK STACKS (t=6s) =====", flush=True)
        for t in asyncio.all_tasks():
            if t is asyncio.current_task():
                continue
            t.print_stack(limit=8)
        print("===== END STACKS =====", flush=True)

    wd = asyncio.create_task(watchdog())
    try:
        outputs = await asyncio.wait_for(rt.run(), timeout=15)
        print("STATUS:", rt.status)
        print("OUTPUTS:", outputs)
    except asyncio.TimeoutError:
        print("TIMEOUT: run() hung >15s")
    finally:
        wd.cancel()


if __name__ == "__main__":
    asyncio.run(main())
