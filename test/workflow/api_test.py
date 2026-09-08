# -*- coding: utf-8 -*-
"""workflow 后端 API 全量细节测试（经网关 18000）"""
import json
import sys
import time
import requests

BASE = "http://127.0.0.1:18000/api/workflow"
GW = "http://127.0.0.1:18000"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + f" | {name}" + (f" | {str(detail)[:200]}" if not ok else ""))


def ok(resp):
    """按项目规范判成功：HTTP 200 且 body.code==200"""
    try:
        return resp.status_code == 200 and resp.json().get("code") == 200
    except Exception:
        return False


def main():
    s = requests.Session()
    s.trust_env = False
    # 登录
    r = s.post(f"{GW}/api/login/login", json={"username": "e2etest", "password": "E2eTest@2026"}, timeout=15)
    token = r.json()["data"]["token"]
    s.headers["Authorization"] = f"Bearer {token}"
    check("登录", bool(token))

    # 1. 节点定义
    r = s.get(f"{BASE}/workflows/node-definitions", timeout=15)
    defs = r.json()
    cnt = len(defs.get("data") or [])
    check("节点定义列表", r.status_code == 200 and cnt > 0, f"{cnt} 种节点")

    # 2. 测试图：START → TEMPLATE → END（引擎图契约：配置在 node.data、位置 node.position）
    graph = {
        "nodes": [
            {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 200}, "data": {
                "fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": "你好"}]}},
            {"id": "n_tpl", "type": "TEMPLATE", "label": "模板", "position": {"x": 400, "y": 200}, "data": {
                "template": "回声:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "output"}},
            {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 200}, "data": {
                "outputs": [{"name": "answer", "value": "{{n_tpl.text}}"}]}},
        ],
        "edges": [
            {"id": "e1", "source": "n_start", "target": "n_tpl"},
            {"id": "e2", "source": "n_tpl", "target": "n_end"},
        ],
    }
    r = s.post(f"{BASE}/workflows", json={"name": "api测试流程", "description": "API 细节测试"}, timeout=15)
    body = r.json()
    data = body.get("data")
    wf_id = data if isinstance(data, int) else (data or {}).get("id") if data else None
    check("创建工作流", ok(r), body)
    if not wf_id:
        return

    # 3. 更新草稿（保存图）
    r = s.put(f"{BASE}/workflows/{wf_id}", json={"name": "api测试流程", "description": "改", "graph": graph}, timeout=15)
    check("更新草稿", ok(r), r.text[:200])

    # 4. 详情
    r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
    detail = (r.json().get("data") or {})
    check("获取详情", ok(r) and detail.get("graph") is not None, r.text[:200])
    check("详情含图数据", bool((detail.get("graph") or {}).get("nodes")))

    # 5. 校验
    r = s.get(f"{BASE}/workflows/{wf_id}/validate", timeout=15)
    check("图校验", ok(r) and r.json().get("data") is not None, r.text[:200])

    # 6. 发布
    r = s.post(f"{BASE}/workflows/{wf_id}/publish", timeout=15)
    pub = r.json()
    check("发布", ok(r), pub)
    print(f"    发布详情: {json.dumps(pub, ensure_ascii=False)[:200]}")

    # 7. 版本历史
    r = s.get(f"{BASE}/workflows/{wf_id}/versions", timeout=15)
    vers = r.json().get("data") or []
    check("版本历史", ok(r) and len(vers) >= 1, r.text[:200])

    # 8. execute-async 统一异步执行（需求 4:同步 /execute 已取消,接口返回 executionId）
    EXE = f"{GW}/api/workflow/workflow-executions"
    r = s.post(f"{EXE}/workflows/{wf_id}/execute-async", json={"inputs": {"query": "在吗"}}, timeout=30)
    exe = r.json()
    exe_data = exe.get("data")
    exe_id = exe_data if isinstance(exe_data, str) else (exe_data or {}).get("executionId") or (exe_data or {}).get("id")
    check("异步执行返回 executionId", ok(r) and bool(exe_id), json.dumps(exe, ensure_ascii=False)[:200])

    # 9. 轮询执行记录直到终态（status/outputs/errorMessage 从详情读取）
    exe_final = {}
    if exe_id:
        deadline = time.time() + 120
        while time.time() < deadline:
            exe_final = s.get(f"{EXE}/{exe_id}", timeout=15).json().get("data") or {}
            if str(exe_final.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
    print(f"    执行结果: {json.dumps(exe_final, ensure_ascii=False)[:300]}")
    exe_ok = exe_final.get("status") in ("SUCCEEDED", "SUCCESS", "COMPLETED")
    check("异步执行完成", exe_ok, json.dumps(exe_final, ensure_ascii=False)[:300])

    # 10. 执行历史分页
    r = s.get(f"{EXE}/page?current=1&size=10", timeout=15)
    hist = r.json()
    check("执行历史分页", ok(r) and (hist.get("data") or {}).get("records") is not None, r.text[:200])

    if exe_id:
        r = s.get(f"{EXE}/{exe_id}", timeout=15)
        check("执行详情", ok(r) and r.json().get("data"), r.text[:200])
        r = s.get(f"{EXE}/{exe_id}/node-executions", timeout=15)
        nes = r.json().get("data") or []
        check("节点执行明细", ok(r) and len(nes) >= 3, r.text[:200])
        r = s.get(f"{EXE}/{exe_id}/snapshot", timeout=15)
        check("执行快照", ok(r) and r.json().get("data") is not None, r.text[:200])

    # 11. 复制
    r = s.post(f"{BASE}/workflows/{wf_id}/copy", timeout=15)
    cp = r.json()
    cp_data = cp.get("data")
    cp_id = cp_data if isinstance(cp_data, int) else (cp_data or {}).get("id") if cp_data else None
    check("复制工作流", ok(r) and cp_id, cp)

    # 12. 回滚到 v1
    r = s.post(f"{BASE}/workflows/{wf_id}/rollback/1", timeout=15)
    check("回滚版本", ok(r), r.text[:200])

    # 13. 删除
    if cp_id:
        r = s.delete(f"{BASE}/workflows/{cp_id}", timeout=15)
        check("删除", ok(r), r.text[:200])

    # 14. 列表分页（GET + current/size；POST 未定义，预期 405）
    r = s.get(f"{BASE}/workflows/page?current=1&size=5", timeout=15)
    pg = r.json()
    check("列表分页GET", ok(r) and (pg.get("data") or {}).get("records") is not None, r.text[:200])
    r = s.post(f"{BASE}/workflows/page", json={"page": 1, "page_size": 5}, timeout=15)
    check("列表分页POST(预期405)", r.status_code == 405, f"HTTP {r.status_code}")

    # 15. 错误分支测试
    r = s.get(f"{BASE}/workflows/999999", timeout=15)
    check("不存在的工作流(应404/错误码)", r.status_code in (200, 404) and (r.json().get("code") != 200 or r.status_code == 404), r.text[:150])

    # 清理测试工作流
    s.delete(f"{BASE}/workflows/{wf_id}", timeout=15)

    print("\n===== 汇总 =====")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed}/{len(results)} PASS")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
