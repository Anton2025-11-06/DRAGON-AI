# -*- coding: utf-8 -*-
"""登录拿 token，核查技能/工具/MCP/菜单/模型接口"""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:18000"

def call(path, method="GET", token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"raw": str(e)}

# 1. 登录（admin）
st, data = call("/api/login/login", "POST", body={"username": "admin", "password": "Admin@123"})
print(f"login: [{st}] {json.dumps(data, ensure_ascii=False)[:220]}")
token = None
if st == 200:
    token = (data.get("data") or {}).get("token") or data.get("access_token")
    if not token and isinstance(data.get("data"), dict):
        token = data["data"].get("access_token")
print("token:", (token or "NONE")[:40] + "..." if token else "NONE")

def probe(path, method="GET", body=None):
    st, data = call(path, method, token, body)
    brief = json.dumps(data, ensure_ascii=False)
    print(f"[{st}] {method} {path}\n    {brief[:260]}")

if token:
    probe("/api/system/menus", "GET")
    probe("/api/workflow/skills/page?page=1&page_size=5", "GET")
    probe("/api/workflow/tools/page?page=1&page_size=5", "GET")
    probe("/api/workflow/mcp/page?page=1&page_size=5", "GET")