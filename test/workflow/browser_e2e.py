# -*- coding: utf-8 -*-
"""工作流前端浏览器 E2E 测试（CDP 驱动 Chrome 9222）。

覆盖：列表页/新建向导/编辑器画布/节点面板19种节点/拖拽加节点/属性面板/
保存创建/调试预览执行/未保存离开确认/列表删除确认/JS错误捕获。
"""
import json
import sys
import time

sys.path.insert(0, ".")
from cdp_driver import CDP

GW = "http://127.0.0.1:18000"
FE = "http://localhost:5666"
BASE = f"{GW}/api/workflow"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL") + " | " + name + ("" if ok else f" | {str(detail)[:250]}"))


def jv(c, expr):
    return c.eval(expr)


def wait(c, cond_expr, timeout=15, interval=0.5, desc=""):
    """轮询等待 JS 条件为真。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if c.eval(cond_expr):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def nav(c, url, wait_sec=3.5):
    c.eval(f"location.href = '{url}'")
    time.sleep(wait_sec)


HOOK_JS = """
(function () {
  if (window.__errs) return 'hooked';
  window.__errs = [];
  window.addEventListener('error', function (e) {
    window.__errs.push({type:'error', msg: e.message});
  });
  window.addEventListener('unhandledrejection', function (e) {
    window.__errs.push({type:'rejection', reason: String(e.reason).slice(0,200)});
  });
  var oe = console.error.bind(console);
  console.error = function () {
    var a = [];
    for (var i=0;i<arguments.length;i++){ try { a.push(typeof arguments[i]==='object'?JSON.stringify(arguments[i]).slice(0,200):String(arguments[i]).slice(0,200)); } catch(ex){ a.push(String(arguments[i])); } }
    window.__errs.push({type:'console.error', args:a});
    oe.apply(null, arguments);
  };
  return 'hooked';
})()
"""


def ensure_login(c):
    """确保已登录（否则走 UI 登录表单）。"""
    if wait(c, "!document.querySelector('input[name=username]')", timeout=8):
        return True
    # 出现登录表单 → 填表登录
    c.eval("""
    (function(){
      function setVal(el, v){
        var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        setter.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles:true}));
      }
      setVal(document.querySelector('input[name=username]'), 'admin');
      setVal(document.querySelector('input[name=password]'), 'Admin@123');
      var btn = [...document.querySelectorAll('button')].find(b => b.textContent.indexOf('登录')>=0);
      btn && btn.click();
    })()
    """)
    return wait(c, "!document.querySelector('input[name=username]')", timeout=10)


def main():
    import requests
    http = requests.Session()
    http.trust_env = False
    r = http.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    http.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

    # 清理历史残留的同名测试工作流（避免同名卡片干扰删除验证）
    try:
        r = http.get(f"{BASE}/workflows/page?page=1&page_size=50", timeout=15)
        for w in (r.json().get("data", {}).get("records") or []):
            if w.get("name", "").startswith(("E2E-", "probe-")):
                http.delete(f"{BASE}/workflows/{w['id']}", timeout=15)
    except Exception as e:
        print("清理残留失败(忽略):", e)

    c = CDP()
    created_wf = None
    try:
        # ============ A. 列表页 ============
        nav(c, f"{FE}/agent/workflow")
        check("登录态有效", ensure_login(c))
        c.eval(HOOK_JS)
        check("列表页标题", jv(c, "document.title").find("工作流") >= 0, jv(c, "document.title"))
        check("侧边菜单含工作流编排", "工作流编排" in jv(c, "[...document.querySelectorAll('aside li')].map(li=>li.textContent.trim()).join(',')"))
        check("新建工作流按钮存在", "新建工作流" in jv(c, "[...document.querySelectorAll('button')].map(b=>b.textContent.trim()).join('|')"))
        wait(c, "document.querySelectorAll('.ant-card').length > 0", timeout=10)
        cards_before = jv(c, "document.querySelectorAll('.ant-card').length")
        check("工作流卡片渲染", isinstance(cards_before, int) and cards_before >= 1, cards_before)

        # ============ B. 新建向导 ============
        c.eval("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建工作流').click()")
        check("模板选择弹窗出现", wait(c, "!!document.querySelector('.blank-template')", timeout=6))
        c.eval("document.querySelector('.blank-template').click()")
        check("空白模板进入编辑器", wait(c, "location.pathname === '/agent/workflow/editor'", timeout=8), jv(c, "location.pathname"))
        time.sleep(2)
        c.eval(HOOK_JS)
        check("编辑器 __workflowEditor 挂载", wait(c, "!!window.__workflowEditor", timeout=8))

        # ============ C. 编辑器初始状态 ============
        st = jv(c, "window.__workflowEditor.getState()")
        check("新建模式默认 START/END 两节点", st and st.get("canvasNodeCount") == 2, st)
        check("默认名称为新建工作流", st and st.get("workflowName") == "新建工作流", st and st.get("workflowName"))

        # ============ D. 节点面板 19 种节点 ============
        check("节点面板可见", wait(c, "!!document.querySelector('.node-panel') || document.body.textContent.indexOf('节点面板')>=0", timeout=6))
        labels = jv(c, "[...document.querySelectorAll('.node-panel .node-item .node-label')].map(e=>e.textContent.trim())")
        labels = [x for x in (labels or []) if x]
        expect19 = {"开始", "结束", "大模型", "问题分类器", "参数提取器", "智能体", "条件分支", "循环", "迭代", "并行",
                    "变量赋值", "变量聚合", "模板转换", "代码执行", "列表处理", "文档提取", "知识检索", "HTTP 请求", "工具"}
        got = set(labels)
        check(f"节点面板节点齐全({len(got)}/{len(expect19)})",
              got == expect19,
              {"缺失": sorted(expect19 - got), "多余": sorted(got - expect19)})

        # ============ E. 拖拽添加节点（合成 drop 事件） ============
        n0 = jv(c, "window.__workflowEditor.getState().canvasNodeCount")
        dropped = c.eval("""
        (function(){
          var canvas = document.querySelector('.vue-flow') || document.querySelector('[class*=canvas]');
          if (!canvas) return 'no-canvas';
          var rect = canvas.getBoundingClientRect();
          var dt = new DataTransfer();
          dt.setData('application/vueflow-nodetype', 'TEMPLATE');
          canvas.dispatchEvent(new DragEvent('drop', {
            clientX: rect.left + Math.min(rect.width*0.6, 500),
            clientY: rect.top + Math.min(rect.height*0.5, 260),
            bubbles: true, cancelable: true, dataTransfer: dt
          }));
          return 'dropped';
        })()
        """)
        time.sleep(1.5)
        n1 = jv(c, "window.__workflowEditor.getState().canvasNodeCount")
        check("拖拽添加 TEMPLATE 节点", dropped == "dropped" and n1 == n0 + 1, {"before": n0, "after": n1, "drop": dropped})

        # ============ F. 属性面板 ============
        sel = c.eval("window.__workflowEditor.selectFirstNonBoundaryNode()")
        time.sleep(1)
        check("点击节点选中(TEMPLATE)", sel and sel.get("type") == "TEMPLATE", sel)
        pp_text = jv(c, "document.body.textContent")
        check("属性面板打开并显示模板配置",
              wait(c, "document.body.textContent.indexOf('模板')>=0 && !!document.querySelector('.property-panel, [class*=property]')", timeout=5))
        # 修改模板内容
        edited = c.eval("""
        (function(){
          var ta = document.querySelector('.property-panel textarea, [class*=property] textarea, [class*=property] input');
          if (!ta) return 'no-input';
          var proto = ta.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(ta, 'E2E测试模板:{{n_start.query}}');
          ta.dispatchEvent(new Event('input', {bubbles:true}));
          ta.dispatchEvent(new Event('change', {bubbles:true}));
          return 'edited';
        })()
        """)
        time.sleep(0.8)
        check("属性面板编辑模板内容", edited == "edited", edited)

        # ============ G. 未保存离开确认 ============
        c.eval("[...document.querySelectorAll('button')].find(b=>b.querySelector('.anticon-arrow-left'))?.click() || document.querySelector('.editor-header button').click()")
        check("离开确认弹窗出现", wait(c, "!!document.querySelector('.ant-modal-confirm')", timeout=6))
        c.eval("[...document.querySelectorAll('.ant-modal-confirm button')].find(b=>b.textContent.trim()==='取消')?.click()")
        time.sleep(0.8)
        check("取消后留在编辑器", jv(c, "location.pathname") == "/agent/workflow/editor", jv(c, "location.pathname"))
        c.eval("[...document.querySelectorAll('.editor-header button')].find(b=>b.querySelector('.anticon-arrow-left'))?.click()")
        wait(c, "!!document.querySelector('.ant-modal-confirm')", timeout=6)
        c.eval("[...document.querySelectorAll('.ant-modal-confirm button')].find(b=>b.textContent.trim()==='离开')?.click() || [...document.querySelectorAll('.ant-modal-confirm .ant-btn-primary')].pop().click()")
        check("确认离开返回列表", wait(c, "location.pathname === '/agent/workflow'", timeout=8), jv(c, "location.pathname"))
        time.sleep(2)

        # ============ H. API 建完整工作流 → 编辑器加载 ============
        graph = {
            "nodes": [
                {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 300},
                 "data": {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True,
                                      "defaultValue": "你好"}]}},
                {"id": "n_tpl", "type": "TEMPLATE", "label": "模板", "position": {"x": 400, "y": 300},
                 "data": {"template": "E2E回声:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "text"}},
                {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 300},
                 "data": {"outputs": [{"name": "answer", "value": "{{n_tpl.text}}"}]}},
            ],
            "edges": [
                {"id": "e1", "source": "n_start", "target": "n_tpl"},
                {"id": "e2", "source": "n_tpl", "target": "n_end"},
            ],
        }
        r = http.post(f"{BASE}/workflows", json={"name": "E2E-浏览器测试", "description": "浏览器E2E"}, timeout=15)
        created_wf = r.json().get("data")
        http.put(f"{BASE}/workflows/{created_wf}",
                 json={"name": "E2E-浏览器测试", "description": "浏览器E2E", "graph": graph}, timeout=15)
        nav(c, f"{FE}/agent/workflow")
        time.sleep(1)
        check("列表可见新工作流卡片",
              wait(c, "document.body.textContent.indexOf('E2E-浏览器测试')>=0", timeout=8))
        nav(c, f"{FE}/agent/workflow/editor/{created_wf}")
        check("编辑器加载已有图", wait(c, "window.__workflowEditor && window.__workflowEditor.getState().canvasNodeCount===3", timeout=10),
              jv(c, "window.__workflowEditor ? window.__workflowEditor.getState() : null"))
        st = jv(c, "window.__workflowEditor.getState()")
        check("边渲染正确(2条)", st and st.get("edgeCount") == 2, st)

        # ============ I. 调试预览执行 ============
        r = c.eval("""
        (async function(){
          var d = window.__workflowEditor.debug;
          await d.setInputValues({query: '浏览器E2E'});
          d.runPreview();
          return 'started';
        })()
        """, await_promise=True)
        check("调试运行启动", r == "started", r)
        done = wait(c, "(function(){var r=window.__workflowEditor.debug.getExecutionResult(); return r && (String(r.status).toLowerCase()==='completed'||r.status==='failed'||r.outputs);})()", timeout=40)
        # 注意：返回值是 Vue reactive proxy，CDP returnByValue 直传会变 null，须 JSON 字符串中转
        result_raw = jv(c, "JSON.stringify(window.__workflowEditor.debug.getExecutionResult())")
        result = json.loads(result_raw) if result_raw else None
        outputs = (result or {}).get("outputs") or {}
        check("调试执行完成输出正确",
              done and str((result or {}).get("status", "")).lower() == "completed" and outputs.get("answer") == "E2E回声:浏览器E2E",
              json.dumps(result, ensure_ascii=False)[:300] if result else None)
        traces = jv(c, "window.__workflowEditor.debug.getNodeTraces()")
        check("节点追踪时间线有数据", isinstance(traces, list) and len(traces) >= 1, traces)

        # ============ J. 保存更新 ============
        c.eval("""
        (function(){
          var inp = document.querySelector('.workflow-name-input');
          if (!inp) return 'no-input';
          var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(inp, 'E2E-浏览器测试-改名');
          inp.dispatchEvent(new Event('input', {bubbles:true}));
          return 'renamed';
        })()
        """)
        time.sleep(0.5)
        save_btn = c.eval("""
        (function(){
          var btn = [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存');
          if (!btn) return 'no-btn';
          btn.click();
          return 'clicked';
        })()
        """)
        check("点击保存", save_btn == "clicked", save_btn)
        time.sleep(2.5)
        r = http.get(f"{BASE}/workflows/{created_wf}", timeout=15)
        wf_data = (r.json().get("data") or {})
        check("保存后名称已更新(API 验证)", wf_data.get("name") == "E2E-浏览器测试-改名", wf_data.get("name"))
        nodes_saved = ((wf_data.get("graph") or {}).get("nodes") or [])
        check("保存后图完整(API 验证)", len(nodes_saved) == 3, len(nodes_saved))

        # ============ K. 列表删除确认 ============
        nav(c, f"{FE}/agent/workflow")
        wait(c, "document.querySelectorAll('.ant-card').length > 0", timeout=12)
        # 注入请求监听（页面加载后注入，捕获删除后的刷新请求）
        c.eval("""(function(){
          if (window.__reqs) return;
          window.__reqs = [];
          var of = window.fetch;
          window.fetch = function(){ window.__reqs.push(String(arguments[0])); return of.apply(this, arguments); };
          var oo = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function(m,u){ window.__reqs.push(m+' '+u); return oo.apply(this, arguments); };
        })()""")
        wait(c, "document.body.textContent.indexOf('E2E-浏览器测试-改名')>=0", timeout=10)
        time.sleep(1)
        del_clicked = c.eval("""
        (function(){
          var cards = [...document.querySelectorAll('.ant-card')];
          var card = cards.find(c => c.textContent.indexOf('E2E-浏览器测试-改名') >= 0);
          if (!card) return 'no-card';
          var btns = [...card.querySelectorAll('button, a')];
          var del = btns.find(b => (b.textContent.indexOf('删除')>=0) || (b.className.indexOf('delete')>=0) || (b.querySelector('[class*=delete]')));
          if (del) { del.click(); return 'clicked'; }
          return 'no-del-btn:' + btns.map(b=>b.textContent.trim()).join(',');
        })()
        """)
        check("卡片删除按钮点击", del_clicked == "clicked", del_clicked)
        check("删除确认弹窗", wait(c, "!!document.querySelector('.ant-modal-confirm')", timeout=6))
        c.eval("[...document.querySelectorAll('.ant-modal-confirm .ant-btn-dangerous, .ant-modal-confirm .ant-btn-primary')].pop()?.click()")
        time.sleep(2.5)
        r = http.get(f"{BASE}/workflows/{created_wf}", timeout=15)
        gone = not r.json().get("data") or r.json().get("code") != 200
        card_gone = wait(c, "document.body.textContent.indexOf('E2E-浏览器测试-改名')<0", timeout=12)
        if not (gone and card_gone):
            print("CARDS:", jv(c, "JSON.stringify([...document.querySelectorAll('.ant-card')].map(c=>c.textContent.trim().slice(0,40)))"))
            print("URL:", jv(c, "location.href"))
            print("MODAL:", jv(c, "!!document.querySelector('.ant-modal-confirm')"))
            print("REQS:", jv(c, "JSON.stringify(window.__reqs || [])"))
        check("确认删除后工作流消失", gone and card_gone, {"api_gone": gone, "card_gone": card_gone})
        created_wf = None

        # ============ L. JS 错误捕获 ============
        errs = jv(c, "window.__errs || []") or []
        fatal = [e for e in errs if e.get("type") in ("error", "rejection")]
        check(f"无未捕获 JS 错误({len(errs)} 条捕获)", len(fatal) == 0, fatal[:5])

    finally:
        if created_wf:
            try:
                http.delete(f"{BASE}/workflows/{created_wf}", timeout=15)
            except Exception:
                pass
        c.close()

    print("\n===== 汇总 =====")
    passed = sum(1 for _, o, _ in results if o)
    print(f"{passed}/{len(results)} PASS")
    fails = [(n, d) for n, o, d in results if not o]
    if fails:
        print("失败项:")
        for n, d in fails:
            print(f"  - {n}: {str(d)[:200]}")


if __name__ == "__main__":
    main()
