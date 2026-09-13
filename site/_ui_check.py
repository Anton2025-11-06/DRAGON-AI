# -*- coding: utf-8 -*-
"""确认前端 dev 服务器（Vite）是否可访问。"""
import urllib.request

for url in ("http://localhost:5666/", "http://127.0.0.1:5666/"):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            body = r.read().decode("utf-8", "ignore")
            print(f"{url} -> HTTP {r.status}, bytes={len(body)}")
            print("  has <div id=app>:", "id=\"app\"" in body or "id='app'" in body)
    except Exception as e:
        print(f"{url} -> ERR {type(e).__name__}: {e}")
