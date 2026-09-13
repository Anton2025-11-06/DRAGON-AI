# -*- coding: utf-8 -*-
"""临时诊断 Nacos 鉴权状态：分别用 nacos/nacos、123456/123456 试登录，并测匿名 naming 查询。"""
import urllib.parse
import urllib.request

BASE = "http://121.43.156.100:8848"


def post(path, data):
    url = BASE + path
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"), timeout=8) as r:
            return r.status, r.read().decode("utf-8", "ignore")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:300]
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"


def get(path):
    url = BASE + path
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "ignore")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:300]
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"


print("server version:", get("/nacos/v1/console/server/state"))
print("login nacos/nacos:", post("/nacos/v1/auth/users/login", {"username": "nacos", "password": "nacos"}))
print("login 123456/123456:", post("/nacos/v1/auth/users/login", {"username": "123456", "password": "123456"}))
print("anon list naming:", get("/nacos/v1/ns/service/list?pageNo=1&pageSize=10"))
