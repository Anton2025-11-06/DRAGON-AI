# -*- coding: utf-8 -*-
"""探查：节点面板项结构、保存按钮、调试输出结构"""
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

r = http.post(f"{BASE}/workflows", json={"name": "probe-save-btn", "description": ""}, timeout=15)
wid = r.json().get("data")
print("workflow:", wid)

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
http.put(f"{BASE}/workflows/{wid}", json={"name": "probe-save-btn", "description": "", "graph": graph}, timeout=15)

c = CDP()
try:
    c.eval(f"location.href = '{FE}/agent/workflow/editor/{wid}'")
    time.sleep(6)

    print("=== 1. debug 对象 keys ===")
    print(c.eval("JSON.stringify(Object.keys(window.__workflowEditor.debug))"))
    print("=== 2. 编辑器所有按钮文本 ===")
    print(c.eval("JSON.stringify([...document.querySelectorAll('button')].map(b=>b.textContent.trim()).filter(t=>t))"))
    print("=== 3. 名称输入框 class ===")
    print(c.eval("""JSON.stringify([...document.querySelectorAll('input')].map(i=>({cls:(i.className||'').slice(0,60), val:i.value.slice(0,30)})))"""))
    print("=== 4. 节点面板 item 数量与结构 ===")
    print(c.eval("""(()=>{
      var panel=document.querySelector('.node-panel');
      if(!panel) return 'no .node-panel; classes: '+JSON.stringify([...document.querySelectorAll('[class*=panel]')].map(e=>e.className.slice(0,60)).slice(0,10));
      var items=panel.querySelectorAll('*');
      return 'panel items: ' + panel.querySelectorAll(':scope > *').length;
    })()"""))
    print("=== 5. 面板节点标题（尝试多种选择器） ===")
    print(c.eval("""JSON.stringify({
      nodeItem: document.querySelectorAll('.node-item').length,
      nodeLabel: [...document.querySelectorAll('.node-label')].map(e=>e.textContent.trim()),
      firstItemHTML: (document.querySelector('.node-item')||{}).outerHTML ? document.querySelector('.node-item').outerHTML.slice(0,500) : null
    })"""))
    print("=== 6. runPreview 完整返回 ===")
    print(c.eval("""(async ()=>{
      try {
        var d = window.__workflowEditor.debug;
        await d.setInputValues({query: '探查'});
        var r = await d.runPreview();
        return JSON.stringify({ret: r}).slice(0,500);
      } catch(e) { return 'ERR: ' + e.message; }
    })()""", await_promise=True))
    time.sleep(3)
    print("=== 7. 3秒后 getExecutionResult ===")
    print(c.eval("JSON.stringify(window.__workflowEditor.debug.getExecutionResult()).slice(0,600)"))
    time.sleep(10)
    print("=== 8. 13秒后 getExecutionResult ===")
    print(c.eval("JSON.stringify(window.__workflowEditor.debug.getExecutionResult()).slice(0,600)"))
    print("=== 9. console.error 快照（前端dev日志） ===")
finally:
    try:
        http.delete(f"{BASE}/workflows/{wid}", timeout=15)
    except Exception:
        pass
    c.close()
print("cleanup done")
