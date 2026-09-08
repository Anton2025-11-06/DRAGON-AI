# -*- coding: utf-8 -*-
"""删除后 GET /page 响应体抓取：后端返回旧数据 or 前端渲染未更新"""
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

r = http.post(f"{BASE}/workflows", json={"name": "probe-resp", "description": ""}, timeout=15)
wid = r.json().get("data")

c = CDP()
try:
    c.eval(f"location.href = '{FE}/agent/workflow'")
    time.sleep(5)
    c.eval("""(function(){
      window.__bodies = [];
      var of = window.fetch;
      window.fetch = function(){
        var p = of.apply(this, arguments);
        var u = String(arguments[0]);
        if (u.indexOf('/page') >= 0) {
          p.then(function(r){ return r.clone().text(); }).then(function(t){
            window.__bodies.push({url: u, body: t.slice(0, 500)});
          });
        }
        return p;
      };
    })()""")
    # 点删除 → 确认
    c.eval("""(function(){
      var card = [...document.querySelectorAll('.ant-card')].find(c => c.textContent.indexOf('probe-resp') >= 0);
      var del = [...card.querySelectorAll('button, a')].find(b => b.textContent.indexOf('删除')>=0 || b.className.indexOf('delete')>=0 || b.querySelector('[class*=delete]'));
      del.click();
    })()""")
    time.sleep(1.5)
    c.eval("[...document.querySelectorAll('.ant-modal-confirm .ant-btn-dangerous, .ant-modal-confirm .ant-btn-primary')].pop()?.click()")
    time.sleep(4)
    print("still_on_page:", c.eval("document.body.textContent.indexOf('probe-resp')>=0"))
    print("bodies:", json.dumps(c.eval("JSON.stringify(window.__bodies)"), ensure_ascii=False)[:800])
    # 后端直查：分页接口现在返回什么
    r = http.get(f"{BASE}/workflows/page?page=1&page_size=10", timeout=15)
    names = [w.get("name") for w in (r.json().get("data", {}).get("list") or [])]
    print("backend_page_names:", names)
finally:
    try:
        http.delete(f"{BASE}/workflows/{wid}", timeout=15)
    except Exception:
        pass
    c.close()
print("done")
