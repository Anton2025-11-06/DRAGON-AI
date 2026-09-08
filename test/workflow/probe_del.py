# -*- coding: utf-8 -*-
"""删除后前端列表是否刷新？截图+网络观察"""
import sys, time, json, base64
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

r = http.post(f"{BASE}/workflows", json={"name": "probe-del-refresh", "description": ""}, timeout=15)
wid = r.json().get("data")

c = CDP()
try:
    c.eval(f"location.href = '{FE}/agent/workflow'")
    time.sleep(5)
    # 监听 fetch/XHR
    c.call("Runtime.evaluate", expression="""
    (function(){
      window.__reqs = [];
      var of = window.fetch;
      window.fetch = function(){ var u = arguments[0]; window.__reqs.push(String(u)); return of.apply(this, arguments); };
      var oo = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(m,u){ window.__reqs.push(m+' '+u); return oo.apply(this, arguments); };
      return 'hooked';
    })()
    """, returnByValue=True)
    # 点删除
    clicked = c.eval("""(function(){
      var cards = [...document.querySelectorAll('.ant-card')];
      var card = cards.find(c => c.textContent.indexOf('probe-del-refresh') >= 0);
      if (!card) return 'no-card';
      var btns = [...card.querySelectorAll('button, a')];
      var del = btns.find(b => (b.textContent.indexOf('删除')>=0) || (b.className.indexOf('delete')>=0) || (b.querySelector('[class*=delete]')));
      if (del) { del.click(); return 'clicked'; }
      return 'no-del-btn';
    })()""")
    print("clicked:", clicked)
    time.sleep(1.5)
    c.eval("[...document.querySelectorAll('.ant-modal-confirm .ant-btn-dangerous, .ant-modal-confirm .ant-btn-primary')].pop()?.click()")
    for i in range(6):
        time.sleep(1.5)
        still = c.eval("document.body.textContent.indexOf('probe-del-refresh')>=0")
        reqs = c.eval("JSON.stringify(window.__reqs || [])")
        print(f"t+{(i+1)*1.5}s still_on_page={still} reqs={reqs[:300]}")
        if not still:
            break
    # 截图
    shot = c.call("Page.captureScreenshot")
    with open("del_refresh.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("screenshot saved: del_refresh.png")
finally:
    try:
        http.delete(f"{BASE}/workflows/{wid}", timeout=15)
    except Exception:
        pass
    c.close()
