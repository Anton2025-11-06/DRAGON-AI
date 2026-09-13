# -*- coding: utf-8 -*-
"""验证 MCP / skills 真实接口路径"""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:18000"

def call(path, method="GET", token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}

st, data = call("/api/login/login", "POST", body={"username": "admin", "password": "Admin@123"})
tok = (data.get("data") or {}).get("token")
print("login:", st)

for p in ("/api/workflow/mcp-server/page?page=1&page_size=5",
          "/api/workflow/skills/page?page=1&page_size=5",
          "/api/workflow/mcp-server/test-params",
          "/api/system/me/menus"):
    if p.endswith("test-params"):
        s, d = call(p, "POST", tok, {"name": "x", "type": "SSE"})
    else:
        s, d = call(p, "GET", tok)
    print(f"[{s}] {p}\n    {json.dumps(d, ensure_ascii=False)[:300]}")