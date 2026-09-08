# -*- coding: utf-8 -*-
"""本地直驱引擎复现 HTTP 4xx 路径的 KeyError('"code"')，打印完整 traceback。"""
import asyncio
import sys
import traceback

sys.path.insert(0, "E:/project/DRAGON-AI")

from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.engine import WorkflowRuntime

graph = {
    "nodes": [
        {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 300},
         "data": {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True,
                              "defaultValue": "你好"}]}},
        {"id": "n_http", "type": "HTTP_REQUEST", "label": "HTTP", "position": {"x": 400, "y": 300},
         "data": {"url": "http://127.0.0.1:18000/api/workflow/workflows/node-definitions",
                  "method": "GET", "outputVariable": "response"}},
        {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 300},
         "data": {"outputs": [{"name": "answer", "value": "{{n_http.response}}"}]}},
    ],
    "edges": [
        {"id": "e1", "source": "n_start", "target": "n_http"},
        {"id": "e2", "source": "n_http", "target": "n_end"},
    ],
}


async def main():
    g = WorkflowGraph(graph)
    rt = WorkflowRuntime(g, "repro-http400", inputs={"query": "q"})
    try:
        outputs = await rt.run()
        print("run ok, status:", rt.status)
        print("outputs:", outputs)
        resp = {
            "status": rt.status,
            "outputs": outputs,
            "nodeStates": rt.node_states_dict(),
            "errorMessage": rt.error,
        }
        import json
        print("resp json:", json.dumps(resp, ensure_ascii=False, default=str)[:500])
    except Exception:
        traceback.print_exc()


asyncio.run(main())
