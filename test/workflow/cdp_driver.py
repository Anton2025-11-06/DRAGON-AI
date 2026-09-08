# -*- coding: utf-8 -*-
"""
CDP 极简驱动：绕过 agent-browser daemon（本机 Chrome 152 不写 DevToolsActivePort，
daemon 自启动必然失败）。直接通过 WebSocket 驱动已开启 --remote-debugging-port 的 Chrome。

用法（python cdp_driver.py <js文件> [eval表达式]）：
  python cdp_driver.py eval "location.href"
  python cdp_driver.py eval-file script.js
  python cdp_driver.py nav "http://localhost:5666/xxx"
  python cdp_driver.py screenshot out.png

环境：Chrome 需带 --remote-debugging-port=9222 --user-data-dir=E:/project/DRAGON-AI/test/workflow/chrome-profile 启动
"""
import base64
import json
import sys
import urllib.request

import websocket

CDP_PORT = 9222


def http_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as r:
        return json.loads(r.read().decode())


def get_ws_url():
    # 找到非 about:blank 的页面，没有就取第一个 page 类型 target
    targets = [t for t in http_json("/json/list") if t.get("type") == "page"]
    if not targets:
        raise RuntimeError("无 page target")
    for t in targets:
        if not t["url"].startswith(("about:blank", "devtools://")):
            return t["webSocketDebuggerUrl"]
    return targets[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self):
        self.ws = websocket.create_connection(get_ws_url(), timeout=30)
        self._id = 0

    def call(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # 忽略事件通知

    def eval(self, expr, await_promise=False):
        r = self.call(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=await_promise,
        )
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            desc = d.get("exception", {}).get("description") or d.get("text")
            raise RuntimeError(f"JS 异常: {desc}")
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    cdp = CDP()
    try:
        if cmd == "eval":
            print(json.dumps(cdp.eval(sys.argv[2], await_promise=True), ensure_ascii=False))
        elif cmd == "eval-file":
            with open(sys.argv[2], encoding="utf-8") as f:
                print(json.dumps(cdp.eval(f.read(), await_promise=True), ensure_ascii=False))
        elif cmd == "nav":
            cdp.call("Page.navigate", url=sys.argv[2])
            print("navigated")
        elif cmd == "screenshot":
            r = cdp.call("Page.captureScreenshot")
            with open(sys.argv[2], "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print(f"saved {sys.argv[2]}")
        elif cmd == "inject":
            # 注册脚本，在之后每次页面加载前执行
            with open(sys.argv[2], encoding="utf-8") as f:
                src = f.read()
            cdp.call("Page.addScriptToEvaluateOnNewDocument", source=src)
            print("injected (reload page to take effect)")
        elif cmd == "reload":
            cdp.call("Page.reload")
            print("reloading")
        elif cmd == "run":
            # run <hook.js> <collect_expr> [wait_sec] : 注入hook→刷新→等待→执行采集表达式
            with open(sys.argv[2], encoding="utf-8") as f:
                src = f.read()
            cdp.call("Page.enable")
            cdp.call("Page.addScriptToEvaluateOnNewDocument", source=src)
            cdp.call("Page.reload")
            import time
            time.sleep(float(sys.argv[4]) if len(sys.argv) > 4 else 6.0)
            print(json.dumps(cdp.eval(sys.argv[3], await_promise=True), ensure_ascii=False))
        elif cmd == "targets":
            for t in http_json("/json/list"):
                print(t.get("type"), "|", t.get("url", "")[:80], "|", t.get("title", "")[:40])
        else:
            print("未知命令:", cmd)
            sys.exit(1)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
