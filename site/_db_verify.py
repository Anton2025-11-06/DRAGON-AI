# -*- coding: utf-8 -*-
"""迁移后验证：skills 接口 + 用户菜单树 + 权限码"""
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

s, d = call("/api/workflow/skills/page?page=1&page_size=5", "GET", tok)
print(f"[{s}] skills/page -> {json.dumps(d, ensure_ascii=False)[:200]}")

s, d = call("/api/system/users/me/menus", "GET", tok)
print(f"[{s}] menus")
menus = d.get("data") or d
if isinstance(menus, dict):
    menus = menus.get("menus") or menus.get("list") or []
def walk(nodes, depth=0):
    for n in nodes or []:
        print("  " * depth + f"- {n.get('name')} [{n.get('path')}] type={n.get('type')} comp={(n.get('component') or '')[:42]}")
        walk(n.get("children") or n.get("routes"), depth + 1)
walk(menus if isinstance(menus, list) else [menus])

s, d = call("/api/system/users/me/info", "GET", tok)
perms = []
if s == 200:
    info = d.get("data") or {}
    perms = info.get("permissions") or []
print(f"\n[{s}] me/info permissions count={len(perms)}")
print("  has workflow:skill:list:", "workflow:skill:list" in perms)
print("  has workflow:mcp:list:", "workflow:mcp:list" in perms)
print("  has workflow:tool:list:", "workflow:tool:list" in perms)