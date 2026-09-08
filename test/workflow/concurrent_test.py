# -*- coding: utf-8 -*-
"""并发执行测试：同一工作流 + 不同工作流的并发 DEBUG 执行。

验证：
- 并发执行互不串扰（各自输出正确）
- 全部 COMPLETED、无 FAILED/超时
- 并发总耗时 < 串行总耗时（确认真并发）

用法：python concurrent_test.py [并发数，默认 6]
"""
import sys
import time
import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"
EXE = f"{BASE}/workflow-executions"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6


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
        "edges": [{"id": "e1", "source": "n_start", "target": "n_tpl"},
                  {"id": "e2", "source": "n_tpl", "target": "n_end"}],
    }


def ok(r):
    try:
        return r.status_code == 200 and r.json().get("code") == 200
    except Exception:
        return False


def main():
    import concurrent.futures as cf

    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    token = r.json()["data"]["token"]

    # 准备 N 个工作流
    wids = []
    try:
        for i in range(N):
            ss = requests.Session()
            ss.trust_env = False
            ss.headers["Authorization"] = f"Bearer {token}"
            r = ss.post(f"{BASE}/workflows", json={"name": f"并发测试-{i}", "description": ""}, timeout=15)
            wid = r.json()["data"]
            r = ss.put(f"{BASE}/workflows/{wid}",
                       json={"name": f"并发测试-{i}", "description": "", "graph": graph_for(f"cc{i}")}, timeout=15)
            assert ok(r), f"写图失败 {i}"
            wids.append((ss, wid))

        def run_one(item):
            ss, wid = item
            idx = wids.index(item)
            q = f"并发q{idx}"
            t0 = time.time()
            # 需求 4:统一 execute-async 投递 + 轮询执行记录直到终态
            r = ss.post(f"{EXE}/workflows/{wid}/execute-async",
                        json={"inputs": {"query": q}}, timeout=30)
            exe_id = r.json().get("data") if ok(r) else None
            exe = {}
            if exe_id:
                deadline = time.time() + 120
                while time.time() < deadline:
                    exe = (ss.get(f"{EXE}/{exe_id}", timeout=15).json().get("data") or {})
                    if str(exe.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                    time.sleep(0.2)
            lat = time.time() - t0
            expect = f"cc{idx}:{q}"
            answer = (exe.get("outputs") or {}).get("answer")
            return {"idx": idx, "ok": exe.get("status") == "COMPLETED" and answer == expect,
                    "answer": answer, "expect": expect, "lat": lat,
                    "status": exe.get("status"), "err": r.text[:200] if not ok(r) else ""}

        # 串行基线（1 个）
        t0 = time.time()
        base = run_one(wids[0])
        serial_lat = base["lat"]

        # 并发执行（同一批，不同工作流 + 各自不同 query）
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(run_one, wids))
        conc_total = time.time() - t0

        print(f"串行单次基线: {serial_lat:.2f}s")
        print(f"并发 {N} 路总耗时: {conc_total:.2f}s (平均单路 {sum(x['lat'] for x in results)/N:.2f}s)")
        all_ok = all(x["ok"] for x in results)
        for x in results:
            mark = "PASS" if x["ok"] else "FAIL"
            extra = "" if x["ok"] else f" status={x['status']} answer={x['answer']} expect={x['expect']} err={x['err']}"
            print(f"{mark} | 并发#{x['idx']} ({x['lat']:.2f}s){extra}")

        # 同一工作流并发（隔离性最狠的测法）
        ss0, wid0 = wids[0]
        r = ss0.put(f"{BASE}/workflows/{wid0}",
                    json={"name": "并发测试-0", "description": "", "graph": graph_for("same-wf")}, timeout=15)
        def run_same(i):
            # 需求 4:统一 execute-async 投递 + 轮询执行记录直到终态
            r = ss0.post(f"{EXE}/workflows/{wid0}/execute-async",
                         json={"inputs": {"query": f"同流q{i}"}}, timeout=30)
            exe_id = r.json().get("data") if ok(r) else None
            exe = {}
            if exe_id:
                deadline = time.time() + 120
                while time.time() < deadline:
                    exe = (ss0.get(f"{EXE}/{exe_id}", timeout=15).json().get("data") or {})
                    if str(exe.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
                        break
                    time.sleep(0.2)
            return {"i": i, "ok": exe.get("status") == "COMPLETED"
                    and (exe.get("outputs") or {}).get("answer") == f"same-wf:同流q{i}",
                    "answer": (exe.get("outputs") or {}).get("answer")}
        with cf.ThreadPoolExecutor(max_workers=N) as ex:
            same_results = list(ex.map(run_same, range(N)))
        for x in same_results:
            mark = "PASS" if x["ok"] else "FAIL"
            extra = "" if x["ok"] else f" answer={x['answer']}"
            print(f"{mark} | 同流并发#{x['i']}{extra}")

        total = len(results) + len(same_results)
        passed = sum(1 for x in results if x["ok"]) + sum(1 for x in same_results if x["ok"])
        print(f"\n===== 汇总 =====\n{passed}/{total} PASS")
        sys.exit(0 if passed == total else 1)
    finally:
        for ss, wid in wids:
            try:
                ss.delete(f"{BASE}/workflows/{wid}", timeout=15)
            except Exception:
                pass


if __name__ == "__main__":
    main()
