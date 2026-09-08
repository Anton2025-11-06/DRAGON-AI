# -*- coding: utf-8 -*-
"""
Nacos 临时实例保活脚本（绕过服务端损坏的认证 API）：
- 服务端 v1/v3 登录接口 500，SDK gRPC 注册拿不到 token
- 改用无需认证的 v1 实例 HTTP API 注册 ephemeral 实例，并每 5s 发心跳
- 服务不在或失联时自动重新注册
用法: python nacos_heartbeat.py  （Ctrl+C 或杀进程停止）
"""
import json
import time
import urllib.parse
import urllib.request

NACOS = "http://10.88.129.3:8848"
SERVICE = "service_workflow"
IP = "10.87.106.143"
PORT = 9003
NS = "dragon-ai"
HEARTBEAT_SEC = 5


def _req(method, path, params):
    url = NACOS + path + "?" + urllib.parse.urlencode(params)
    r = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(r, timeout=5) as resp:
        return resp.status, resp.read().decode()


def register():
    st, body = _req("POST", "/nacos/v1/ns/instance", {
        "serviceName": SERVICE, "ip": IP, "port": PORT,
        "namespaceId": NS, "healthy": "true", "enabled": "true",
    })
    return st, body


def beat():
    st, body = _req("PUT", "/nacos/v1/ns/instance/beat", {
        "serviceName": SERVICE, "ip": IP, "port": PORT, "namespaceId": NS,
        "beat": json.dumps({"ip": IP, "port": PORT, "serviceName": SERVICE,
                            "cluster": "DEFAULT", "weight": 1.0}),
    })
    return st, body


def list_instances():
    st, body = _req("GET", "/nacos/v1/ns/instance/list", {
        "serviceName": SERVICE, "namespaceId": NS})
    hosts = json.loads(body).get("hosts", [])
    return any(h["ip"] == IP and h["port"] == PORT and h.get("healthy") for h in hosts)


def main():
    print(f"[{time.strftime('%H:%M:%S')}] heartbeat start {IP}:{PORT} -> {NACOS}", flush=True)
    while True:
        try:
            if not list_instances():
                st, body = register()
                print(f"[{time.strftime('%H:%M:%S')}] re-register: {st} {body[:80]}", flush=True)
            st, body = beat()
            if st != 200 or "ok" not in body:
                print(f"[{time.strftime('%H:%M:%S')}] beat: {st} {body[:80]}", flush=True)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] error: {e}", flush=True)
        time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    main()
