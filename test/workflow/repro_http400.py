# -*- coding: utf-8 -*-
"""HTTP 4xx 失败路径 400 '"code"' 异常复现:完整打印 execute-async + 轮询详情响应。"""
import json
import time
import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"

s = requests.Session()
s.trust_env = False
r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
s.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

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

r = s.post(f"{BASE}/workflows", json={"name": "dbg-http403-v2", "description": "复现"}, timeout=15)
wf_id = r.json().get("data")
print("create:", r.status_code, wf_id)
r = s.put(f"{BASE}/workflows/{wf_id}", json={"name": "dbg-http403-v2", "description": "复现", "graph": graph}, timeout=15)
print("save:", r.status_code, r.json().get("code"))

r = s.post(f"{BASE}/workflow-executions/workflows/{wf_id}/execute-async", json={"inputs": {"query": "q"}}, timeout=30)
print("execute-async HTTP status:", r.status_code)
try:
    body = r.json()
    print("execute-async body code:", body.get("code"), "message:", body.get("message"))
    exe_id = body.get("data")
    # 轮询执行记录直到终态(需求 4:同步接口已取消,结果从详情读取)
    d = {}
    if exe_id:
        deadline = time.time() + 60
        while time.time() < deadline:
            d = (s.get(f"{BASE}/workflow-executions/{exe_id}", timeout=15).json().get("data") or {})
            if str(d.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
    if d:
        print("  exec status:", d.get("status"))
        print("  errorMessage:", str(d.get("errorMessage"))[:300])
        print("  outputs:", json.dumps(d.get("outputs"), ensure_ascii=False)[:300])
        ns = d.get("nodeStates") or {}
        for nid, st in ns.items():
            print(f"  node {nid}: {st.get('status')} err={str(st.get('error'))[:200]}")
    else:
        print("  no execution id returned:", json.dumps(body, ensure_ascii=False)[:300])
except Exception as e:
    print("parse fail:", e, r.text[:500])

s.delete(f"{BASE}/workflows/{wf_id}", timeout=15)
print("cleaned:", wf_id)
