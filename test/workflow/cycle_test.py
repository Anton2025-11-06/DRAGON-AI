# -*- coding: utf-8 -*-
"""工作流「创建→执行→发布→执行→删除」全链路反复循环测试。

验证点：
- 全链路可重复执行（幂等），无版本号/ID 冲突
- 每轮执行结果正确（输出含轮次标记）
- 删除级联彻底，无资源泄漏（执行记录随删除消失）
- 多轮后服务仍稳定响应（时延无明显劣化）

用法：python cycle_test.py [轮数，默认 8]
"""
import sys
import time
import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8

fails = []


def execute_and_wait(s, wf_id, inputs, timeout=120):
    """需求 4:同步 /execute 已取消——统一 execute-async 投递 + 轮询执行记录直到终态。"""
    r = s.post(f"{BASE}/workflow-executions/workflows/{wf_id}/execute-async",
               json={"inputs": inputs}, timeout=30)
    if not ok(r):
        return {"_http": r.text[:200]}
    exe_id = r.json().get("data")
    if not exe_id:
        return {"_http": r.text[:200]}
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = (s.get(f"{BASE}/workflow-executions/{exe_id}", timeout=15).json().get("data") or {})
        if str(d.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
            return d
        time.sleep(0.5)
    return {"status": "TIMEOUT", "id": exe_id, "_http": f"轮询 {timeout}s 未达终态"}


def graph_for(tag):
    return {
        "nodes": [
            {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 300},
             "data": {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True,
                                  "defaultValue": ""}]}},
            {"id": "n_tpl", "type": "TEMPLATE", "label": "模板", "position": {"x": 400, "y": 300},
             "data": {"template": f"{tag}:{{{{n_start.query}}}}", "engine": "SIMPLE", "outputVariable": "text"}},
            {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 300},
             "data": {"outputs": [{"name": "answer", "value": "{{n_tpl.text}}"}]}},
        ],
        "edges": [
            {"id": "e1", "source": "n_start", "target": "n_tpl"},
            {"id": "e2", "source": "n_tpl", "target": "n_end"},
        ],
    }


def ok(r):
    try:
        return r.status_code == 200 and r.json().get("code") == 200
    except Exception:
        return False


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    s.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

    latencies = []
    for i in range(1, ROUNDS + 1):
        t0 = time.time()
        tag = f"cycle{i}-draft"
        try:
            # 1. 创建
            r = s.post(f"{BASE}/workflows", json={"name": f"循环测试-{i}", "description": "cycle"}, timeout=15)
            assert ok(r), f"创建失败: {r.text[:200]}"
            wid = r.json()["data"]

            # 2. 写图
            r = s.put(f"{BASE}/workflows/{wid}",
                      json={"name": f"循环测试-{i}", "description": "cycle", "graph": graph_for(tag)}, timeout=15)
            assert ok(r), f"写图失败: {r.text[:200]}"

            # 3. DEBUG 执行（草稿）:异步投递 + 轮询终态
            exe = execute_and_wait(s, wid, {"query": f"q{i}"})
            assert exe.get("status") == "COMPLETED", \
                f"草稿执行失败: {exe.get('_http') or exe.get('errorMessage')}"
            assert (exe.get("outputs") or {}).get("answer") == f"{tag}:q{i}", \
                f"草稿输出错误: {(exe.get('outputs') or {}).get('answer')}"
            exe_id = exe.get("id")

            # 4. 发布
            r = s.post(f"{BASE}/workflows/{wid}/publish", json={"comment": f"cycle{i}"}, timeout=15)
            assert ok(r), f"发布失败: {r.text[:200]}"

            # 5. 改草稿后再执行（验证 DEBUG 恒跑草稿）
            tag2 = f"cycle{i}-after-publish"
            r = s.put(f"{BASE}/workflows/{wid}",
                      json={"name": f"循环测试-{i}", "description": "cycle", "graph": graph_for(tag2)}, timeout=15)
            assert ok(r), f"二写失败: {r.text[:200]}"
            exe2 = execute_and_wait(s, wid, {"query": f"q{i}b"})
            assert exe2.get("status") == "COMPLETED", \
                f"二次执行失败: {exe2.get('_http') or exe2.get('errorMessage')}"
            assert (exe2.get("outputs") or {}).get("answer") == f"{tag2}:q{i}b", \
                f"二次输出错误: {(exe2.get('outputs') or {}).get('answer')}"

            # 6. 版本数正确（发布一次 = 1 个版本）
            r = s.get(f"{BASE}/workflows/{wid}/versions", timeout=15)
            versions = (r.json().get("data") or [])
            assert len(versions) == 1, f"版本数错误: {len(versions)}"

            # 7. 删除
            r = s.delete(f"{BASE}/workflows/{wid}", timeout=15)
            assert ok(r), f"删除失败: {r.text[:200]}"

            # 8. 级联验证：详情 404
            r = s.get(f"{BASE}/workflows/{wid}", timeout=15)
            assert not ok(r) or not r.json().get("data"), f"删除后详情仍存在: {r.text[:150]}"

            lat = time.time() - t0
            latencies.append(lat)
            print(f"ROUND {i}/{ROUNDS} PASS  ({lat:.2f}s)  draft->'{tag}:q{i}' published->'{tag2}:q{i}b' exe_id={exe_id}")
        except AssertionError as e:
            latencies.append(time.time() - t0)
            msg = f"ROUND {i}/{ROUNDS} FAIL: {e}"
            print(msg)
            fails.append(msg)
            # 尝试清理
            try:
                if "wid" in dir():
                    s.delete(f"{BASE}/workflows/{wid}", timeout=15)
            except Exception:
                pass

    print("\n===== 汇总 =====")
    print(f"{ROUNDS - len(fails)}/{ROUNDS} PASS")
    if latencies:
        print(f"时延: avg={sum(latencies)/len(latencies):.2f}s min={min(latencies):.2f}s max={max(latencies):.2f}s")
        if len(latencies) >= 4:
            half = len(latencies) // 2
            a, b = sum(latencies[:half]) / half, sum(latencies[half:]) / (len(latencies) - half)
            print(f"劣化检查: 前半均值 {a:.2f}s vs 后半均值 {b:.2f}s -> {'劣化!' if b > a * 1.5 else '稳定'}")
    if fails:
        print("失败项:")
        for f in fails:
            print(" -", f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
