# -*- coding: utf-8 -*-
"""最小复现：调试执行后 getExecutionResult 为何为 None"""
import sys, time, json
sys.path.insert(0, r"E:/project/DRAGON-AI/test/workflow")
from cdp_driver import CDP
import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"
FE = "http://localhost:5666"

http = requests.Session()
http.trust_env = False
r = http.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
http.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

r = http.post(f"{BASE}/workflows", json={"name": "probe-debug", "description": ""}, timeout=15)
wid = r.json().get("data")
graph = {
    "nodes": [
        {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 300},
         "data": {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": "你好"}]}},
        {"id": "n_tpl", "type": "TEMPLATE", "label": "模板", "position": {"x": 400, "y": 300},
         "data": {"template": "E2E回声:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "text"}},
        {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 300},
         "data": {"outputs": [{"name": "answer", "value": "{{n_tpl.text}}"}]}},
    ],
    "edges": [{"id": "e1", "source": "n_start", "target": "n_tpl"}, {"id": "e2", "source": "n_tpl", "target": "n_end"}],
}
http.put(f"{BASE}/workflows/{wid}", json={"name": "probe-debug", "description": "", "graph": graph}, timeout=15)

c = CDP()
try:
    c.eval(f"location.href = '{FE}/agent/workflow/editor/{wid}'")
    time.sleep(6)
    # 模拟 E2E 的启动方式（不 await runPreview，fire-and-forget）
    print("start:", c.eval("""(async function(){
      var d = window.__workflowEditor.debug;
      await d.setInputValues({query: '浏览器E2E'});
      d.runPreview();
      return 'started';
    })()""", await_promise=True))
    for i in range(12):
        time.sleep(1)
        snap = c.eval("""(function(){
      var d = window.__workflowEditor.debug;
      return JSON.stringify({res: d.getExecutionResult(), traces: (d.getNodeTraces()||[]).length});
    })()""")
        print(f"t+{i+1}s:", (snap or "")[:200])
        if snap and '"res":{' in snap and 'null' not in snap.split('"res":')[1][:5]:
            break
finally:
    try:
        http.delete(f"{BASE}/workflows/{wid}", timeout=15)
    except Exception:
        pass
    c.close()
print("done")
