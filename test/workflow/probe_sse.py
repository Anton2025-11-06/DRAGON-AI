# -*- coding: utf-8 -*-
"""F1 探针：PAUSED 态订阅 SSE——对比网关(18000)与直连服务(9003)的首帧到达时间。
若网关首帧迟迟不到而直连立即到 → 网关 SSE 非流式转发实锤。"""
import json
import sys
import time

import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"
EXE = f"{BASE}/workflow-executions"

sys.path.insert(0, ".")
from control_test import wf1_graph, node, edge  # noqa: E402


def probe_first_frame(url, eid, token, timeout=6):
    """连接 SSE，返回首帧文本与耗时；超时返回 None。"""
    t0 = time.monotonic()
    try:
        with requests.get(f"{url}/{eid}/subscribe", stream=True, timeout=(3, timeout),
                          headers={"Authorization": f"Bearer {token}"}) as r:
            for raw in r.iter_lines(decode_unicode=True):
                if raw:
                    return raw, round(time.monotonic() - t0, 2)
    except Exception as e:
        return f"<err {type(e).__name__}>", round(time.monotonic() - t0, 2)
    return None, round(time.monotonic() - t0, 2)


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    token = r.json()["data"]["token"]
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.post(f"{BASE}/workflows", json={"name": "F1探针", "description": ""}, timeout=15)
    wid = r.json()["data"]
    s.put(f"{BASE}/workflows/{wid}", json={"name": "F1探针", "description": "", "graph": wf1_graph()}, timeout=15)

    r = s.post(f"{EXE}/workflows/{wid}/execute-async",
               json={"inputs": {"query": "q"}, "breakpoints": ["n_b"]}, timeout=30)
    eid = r.json()["data"]
    for _ in range(30):
        d = s.get(f"{EXE}/{eid}", timeout=10).json().get("data") or {}
        if d.get("status") == "PAUSED":
            break
        time.sleep(0.3)
    print("status:", d.get("status"), "eid:", eid)

    print("网关 18000 首帧:", probe_first_frame(f"{EXE}", eid, token))
    # 服务直连（两种前缀试探）
    for prefix in ("/api/workflow/workflow-executions", "/workflow-executions"):
        url = f"http://127.0.0.1:9003{prefix}"
        try:
            rr = requests.get(f"{url}/{eid}/subscribe", stream=True, timeout=(3, 2),
                              headers={"Authorization": f"Bearer {token}"})
            print(f"直连 9003 前缀 {prefix}: HTTP {rr.status_code}")
            rr.close()
        except Exception as e:
            print(f"直连 9003 前缀 {prefix}: {type(e).__name__}")

    s.delete(f"{BASE}/workflows/{wid}", timeout=15)


if __name__ == "__main__":
    main()
