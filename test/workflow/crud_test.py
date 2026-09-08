# -*- coding: utf-8 -*-
"""工作流 CRUD 全流程 API 测试：创建/详情/分页/更新/校验/发布/版本/复制/回滚/删除。"""
import json
import time
import requests

GW = "http://127.0.0.1:18000"
BASE = f"{GW}/api/workflow"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + " | " + name + ("" if ok else f" | {str(detail)[:220]}"))


def ok(r):
    try:
        return r.status_code == 200 and r.json().get("code") == 200
    except Exception:
        return False


def execute_and_wait(s, wf_id, inputs, timeout=120):
    """需求 4:同步 /execute 已取消——统一 execute-async 投递 + 轮询执行记录直到终态。

    返回执行记录详情(data 部分),含 status/outputs/errorMessage 字段。
    """
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


def simple_graph(tpl_text="CRUD测试"):
    """最小合法图：START → TEMPLATE → END。"""
    return {
        "nodes": [
            {"id": "n_start", "type": "START", "label": "开始", "position": {"x": 100, "y": 300},
             "data": {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True,
                                  "defaultValue": "你好"}]}},
            {"id": "n_tpl", "type": "TEMPLATE", "label": "模板", "position": {"x": 400, "y": 300},
             "data": {"template": f"{tpl_text}:{{{{n_start.query}}}}", "engine": "SIMPLE", "outputVariable": "text"}},
            {"id": "n_end", "type": "END", "label": "结束", "position": {"x": 700, "y": 300},
             "data": {"outputs": [{"name": "answer", "value": "{{n_tpl.text}}"}]}},
        ],
        "edges": [
            {"id": "e1", "source": "n_start", "target": "n_tpl"},
            {"id": "e2", "source": "n_tpl", "target": "n_end"},
        ],
    }


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    s.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

    wf_id = None
    try:
        # ============ 1. 创建 ============
        r = s.post(f"{BASE}/workflows", json={"name": "CRUD全流程测试", "description": " CRUD 测试工作流"}, timeout=15)
        check("创建工作流", ok(r), r.text[:200])
        wf_id = r.json().get("data")
        check("创建返回 ID", isinstance(wf_id, int) and wf_id > 0, wf_id)

        # ============ 2. 详情 ============
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        check("获取详情", ok(r) and r.json()["data"]["name"] == "CRUD全流程测试", r.text[:200])
        detail = r.json()["data"]
        check("详情含 graph/status/currentVersion 字段",
              "graph" in detail and "status" in detail and "currentVersion" in detail, list(detail.keys()))

        # ============ 3. 分页查询 ============
        r = s.get(f"{BASE}/workflows/page?current=1&size=50", timeout=15)
        d = r.json().get("data") or {}
        names = [x.get("name") for x in (d.get("records") or [])]
        check("分页查询可见新工作流", ok(r) and "CRUD全流程测试" in names, names[:10])
        check("分页返回 total 字段", isinstance(d.get("total"), int), d.get("total"))

        # ============ 4. 更新草稿（保存图） ============
        r = s.put(f"{BASE}/workflows/{wf_id}",
                  json={"name": "CRUD全流程测试", "description": "已保存图", "graph": simple_graph("v1")},
                  timeout=15)
        check("更新草稿保存图", ok(r), r.text[:200])
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        nodes = ((r.json()["data"] or {}).get("graph") or {}).get("nodes") or []
        check("草稿图已持久化", len(nodes) == 3, len(nodes))

        # ============ 5. 校验 ============
        r = s.get(f"{BASE}/workflows/{wf_id}/validate", timeout=15)
        check("校验合法图无 ERROR", ok(r) and not [i for i in (r.json()["data"] or {}).get("issues", [])
                                                   if i.get("severity") == "ERROR"], r.text[:300])

        # ============ 6. 执行草稿（DEBUG 触发跑草稿） ============
        exe = execute_and_wait(s, wf_id, {"query": "世界"})
        check("DEBUG 执行草稿图", exe.get("status") == "COMPLETED"
              and (exe.get("outputs") or {}).get("answer") == "v1:世界", exe)

        # ============ 7. 发布（生成版本 1） ============
        r = s.post(f"{BASE}/workflows/{wf_id}/publish", json={"comment": "首个版本"}, timeout=15)
        check("发布 v1", ok(r), r.text[:300])
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        check("currentVersion=1", r.json()["data"]["currentVersion"] == 1, r.json()["data"].get("currentVersion"))

        # ============ 8. 修改草稿 + 再发布（版本 2） ============
        r = s.put(f"{BASE}/workflows/{wf_id}",
                  json={"name": "CRUD全流程测试", "description": "已保存图", "graph": simple_graph("v2")}, timeout=15)
        check("更新草稿到 v2", ok(r), r.text[:200])
        r = s.post(f"{BASE}/workflows/{wf_id}/publish", json={"comment": "第二个版本"}, timeout=15)
        check("发布 v2", ok(r), r.text[:300])
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        check("currentVersion=2", r.json()["data"]["currentVersion"] == 2, r.json()["data"].get("currentVersion"))

        # ============ 9. 执行语义：DEBUG 触发始终跑草稿 ============
        # （WorkflowExecutionReq 无 triggerType 字段；快照执行路径预留给 API Key 外部调用，当前无路由入口）
        r = s.put(f"{BASE}/workflows/{wf_id}",
                  json={"name": "CRUD全流程测试", "description": "已保存图", "graph": simple_graph("v3-draft")}, timeout=15)
        check("草稿改到 v3(未发布)", ok(r), r.text[:200])
        exe = execute_and_wait(s, wf_id, {"query": "世界"})
        answer = (exe.get("outputs") or {}).get("answer")
        check("执行跑草稿(v3-draft)", exe.get("status") == "COMPLETED"
              and answer == "v3-draft:世界", f"answer={answer}")

        # ============ 10. 版本历史 ============
        r = s.get(f"{BASE}/workflows/{wf_id}/versions", timeout=15)
        vers = r.json().get("data") or []
        check("版本历史含 v1/v2", ok(r) and {v.get("version") for v in vers} >= {1, 2}, vers)

        # ============ 11. 指定版本快照 ============
        r = s.get(f"{BASE}/workflows/{wf_id}/versions/1", timeout=15)
        snap = r.json().get("data") or {}
        snap_tpl = json.dumps(snap, ensure_ascii=False)
        check("v1 快照内容正确", ok(r) and "v1:" in snap_tpl, snap_tpl[:120])

        # ============ 12. 回滚到 v1（设计：覆盖草稿并重新发布为新版本） ============
        r = s.post(f"{BASE}/workflows/{wf_id}/rollback/1", timeout=15)
        check("回滚到 v1", ok(r), r.text[:300])
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        d = r.json()["data"]
        check("回滚后草稿=v1 且重发为新版本 v3",
              d.get("currentVersion") == 3 and "v1:" in json.dumps(d.get("graph") or {}, ensure_ascii=False),
              {"currentVersion": d.get("currentVersion")})

        # ============ 13. 复制 ============
        r = s.post(f"{BASE}/workflows/{wf_id}/copy", timeout=15)
        check("复制工作流", ok(r), r.text[:300])
        copy_id = r.json().get("data")
        if isinstance(copy_id, dict):
            copy_id = copy_id.get("id")
        if copy_id:
            r = s.get(f"{BASE}/workflows/{copy_id}", timeout=15)
            cd = r.json()["data"]
            check("副本独立存在且内容一致",
                  cd.get("name") != "CRUD全流程测试" and
                  "v1:" in json.dumps(cd.get("graph") or {}, ensure_ascii=False),
                  {"name": cd.get("name")})
            # 副本执行可用（异步投递 + 轮询终态）
            exe = execute_and_wait(s, copy_id, {"query": "副本"})
            check("副本可执行", exe.get("status") == "COMPLETED", exe)
            s.delete(f"{BASE}/workflows/{copy_id}", timeout=15)
            r = s.get(f"{BASE}/workflows/{copy_id}", timeout=15)
            check("删除副本成功(详情 404/空)", not ok(r) or r.json().get("code") != 200, r.text[:150])
        else:
            check("复制返回 ID", False, copy_id)

        # ============ 14. 删除（级联） ============
        r = s.delete(f"{BASE}/workflows/{wf_id}", timeout=15)
        check("删除工作流", ok(r), r.text[:200])
        r = s.get(f"{BASE}/workflows/{wf_id}", timeout=15)
        check("删除后详情不可见", not ok(r) or r.json().get("code") != 200, r.text[:150])
        r = s.get(f"{BASE}/workflows/{wf_id}/versions", timeout=15)
        check("删除后版本历史清空", not ok(r) or not (r.json().get("data") or []), r.text[:150])
        r = s.get(f"{BASE}/workflows/page?current=1&size=100", timeout=15)
        names = [x.get("name") for x in ((r.json().get("data") or {}).get("records") or [])]
        check("删除后列表不可见", "CRUD全流程测试" not in names, names[:10])

    finally:
        # 兜底清理
        if wf_id:
            s.delete(f"{BASE}/workflows/{wf_id}", timeout=15)

    # ============ 15. 负向用例 ============
    r = s.get(f"{BASE}/workflows/999999", timeout=15)
    check("查询不存在的工作流报错", not ok(r) or r.json().get("code") != 200, r.text[:150])
    r = s.put(f"{BASE}/workflows/999999", json={"name": "x", "graph": simple_graph()}, timeout=15)
    check("更新不存在的工作流报错", not ok(r) or r.json().get("code") != 200, r.text[:150])
    r = s.delete(f"{BASE}/workflows/999999", timeout=15)
    check("删除不存在的工作流报错", not ok(r) or r.json().get("code") != 200, r.text[:150])
    r = s.post(f"{BASE}/workflows", json={"name": ""}, timeout=15)
    check("创建空名校验拦截", not ok(r) or r.json().get("code") != 200, r.text[:150])

    print("\n===== 汇总 =====")
    passed = sum(1 for _, o, _ in results if o)
    print(f"{passed}/{len(results)} PASS")
    fails = [n for n, o, _ in results if not o]
    if fails:
        print("失败项:")
        for f in fails:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
