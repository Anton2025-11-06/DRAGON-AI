# -*- coding: utf-8 -*-
"""网关接口现状核查（无需登录）"""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:18000"

def probe(path: str, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                data = json.loads(body)
                brief = json.dumps(data, ensure_ascii=False)[:300]
            except Exception:
                brief = body[:300]
            print(f"[{resp.status}] {method} {path}\n    {brief}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
            brief = json.dumps(data, ensure_ascii=False)[:300]
        except Exception:
            brief = body[:300]
        print(f"[{e.code}] {method} {path}\n    {brief}")
    except Exception as e:
        print(f"[ERR] {method} {path}\n    {e}")

probe("/api/workflow/skills/page?page=1&page_size=5")
probe("/api/workflow/tools/page?page=1&page_size=5")
probe("/api/workflow/mcp/page?page=1&page_size=5")
probe("/api/system/menus")
probe("/api/workflow/menus")
probe("/api/system/menu")
probe("/api/system/categories")
probe("/api/model/plaza/detail")