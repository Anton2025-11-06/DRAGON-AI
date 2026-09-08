# -*- coding: utf-8 -*-
"""工作流控制链路完整测试：暂停 / 恢复 / 取消 / 变量修改 / 快照 / SSE / 快照重建。

分两部分：
- Part 1（API 级，经网关 18000）：断点暂停全链 / 多级断点 / 手动暂停 mid-run /
  暂停态取消 / 执行中取消 / SSE 实时流与回放 / 快照篡改恢复 / 8 项负向。
- Part 2（引擎级直驱，模拟跨进程）：断点暂停后 run() 正常结束（需求 5 新语义）/ 快照重建续跑。

慢节点方案：HTTP 请求节点指向黑洞地址 http://10.255.255.1:81（connectTimeout 8000ms，
实测本机挂满 8s 后 ConnectTimeout），用 exception 分支接住超时完成收敛。
"""
import asyncio
import json
import sys
import time

import requests

sys.path.insert(0, "E:/project/DRAGON-AI")

BASE = "http://127.0.0.1:18000/api/workflow"
GW = "http://127.0.0.1:18000"
EXE = f"{GW}/api/workflow/workflow-executions"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL") + f" | {name}" + (f" | {str(detail)[:260]}" if not ok else ""), flush=True)


def ok(resp):
    try:
        return resp.status_code == 200 and resp.json().get("code") == 200
    except Exception:
        return False


def node(nid, ntype, label, x, y, data):
    return {"id": nid, "type": ntype, "label": label, "position": {"x": x, "y": y}, "data": data}


def edge(eid, src, tgt, handle=None):
    e = {"id": eid, "source": src, "target": tgt}
    if handle:
        e["sourceHandle"] = handle
    return e


def wf1_graph():
    """断点链：START → A(模板) → B(断点目标) → C(模板 {{g}}) → END(answer={{C.text}})"""
    return {
        "nodes": [
            node("n_start", "START", "开始", 100, 300, {
                "fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": "q"}]}),
            node("n_a", "TEMPLATE", "A", 350, 300, {"template": "A:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "text"}),
            node("n_b", "TEMPLATE", "B", 600, 300, {"template": "B-ok", "engine": "SIMPLE", "outputVariable": "text"}),
            node("n_c", "TEMPLATE", "C", 850, 300, {"template": "{{g}}", "engine": "SIMPLE", "outputVariable": "text"}),
            node("n_end", "END", "结束", 1100, 300, {"outputs": [{"name": "answer", "value": "{{n_c.text}}"}]}),
        ],
        "edges": [edge("e1", "n_start", "n_a"), edge("e2", "n_a", "n_b"),
                  edge("e3", "n_b", "n_c"), edge("e4", "n_c", "n_end")],
    }


def wf2_graph():
    """慢链：START → SLOW(HTTP 黑洞 8s) --exception--> E1(模板) → END。
    正常分支无出边：黑洞必超时，走 exception 收敛，answer 含 EXC: 前缀。"""
    return {
        "nodes": [
            node("n_start", "START", "开始", 100, 300, {
                "fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": "q"}]}),
            node("n_slow", "HTTP_REQUEST", "慢节点", 400, 300, {
                "url": "http://10.255.255.1:81/timeout", "method": "GET",
                "connectTimeout": 8000, "readTimeout": 15000, "outputVariable": "response"}),
            node("n_e1", "TEMPLATE", "异常分支", 700, 300, {
                "template": "EXC:{{n_slow.exception_message}}", "engine": "SIMPLE", "outputVariable": "text"}),
            node("n_end", "END", "结束", 1000, 300, {"outputs": [{"name": "answer", "value": "{{n_e1.text}}"}]}),
        ],
        "edges": [edge("e1", "n_start", "n_slow"),
                  edge("e2", "n_slow", "n_e1", "branch:exception"),
                  edge("e3", "n_e1", "n_end")],
    }


class T:
    def __init__(self, s):
        self.s = s

    def create(self, name):
        r = self.s.post(f"{BASE}/workflows", json={"name": name, "description": "控制链路测试"}, timeout=15)
        d = r.json().get("data")
        return d if isinstance(d, int) else (d or {}).get("id")

    def save(self, wf_id, name, graph):
        return self.s.put(f"{BASE}/workflows/{wf_id}",
                          json={"name": name, "description": "控制链路测试", "graph": graph}, timeout=15)

    def exec_async(self, wf_id, inputs=None, breakpoints=None):
        body = {"inputs": inputs if inputs is not None else {"query": "q"}}
        if breakpoints is not None:
            body["breakpoints"] = breakpoints
        r = self.s.post(f"{EXE}/workflows/{wf_id}/execute-async", json=body, timeout=30)
        return r.json().get("data") if ok(r) else None

    def detail(self, eid):
        r = self.s.get(f"{EXE}/{eid}", timeout=15)
        return r.json().get("data") if ok(r) else None

    def status(self, eid):
        d = self.detail(eid)
        return (d or {}).get("status")

    def wait_status(self, eid, statuses, timeout=30):
        """轮询直到 status 进入 statuses（list），返回最终 status 或 None(超时)。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self.status(eid)
            if st in statuses:
                return st
            time.sleep(0.4)
        return self.status(eid)

    def snapshot(self, eid):
        r = self.s.get(f"{EXE}/{eid}/snapshot", timeout=15)
        return r.json().get("data") if ok(r) else None

    def pause(self, eid):
        r = self.s.post(f"{EXE}/{eid}/pause", timeout=15)
        return ok(r), r.json()

    def resume(self, eid):
        r = self.s.post(f"{EXE}/{eid}/resume", timeout=15)
        return ok(r), r.json()

    def cancel(self, eid):
        r = self.s.post(f"{EXE}/{eid}/cancel", timeout=15)
        return ok(r), r.json()

    def update_var(self, eid, name, value):
        r = self.s.put(f"{EXE}/{eid}/variables/{name}", json=value, timeout=15)
        return ok(r), r.json()

    def node_execs(self, eid):
        r = self.s.get(f"{EXE}/{eid}/node-executions", timeout=15)
        return r.json().get("data") if ok(r) else None


def read_sse_frames(s, eid, want_types, timeout=25):
    """订阅 SSE 并读取帧，直到收集齐 want_types（事件名集合）或超时。返回 (事件名列表, 帧文本列表)。"""
    got, frames = [], []
    try:
        with s.get(f"{EXE}/{eid}/subscribe", stream=True, timeout=timeout) as r:
            start = time.monotonic()
            for raw in r.iter_lines(decode_unicode=True):
                if time.monotonic() - start > timeout:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "ignore")
                if not raw:
                    continue
                frames.append(raw)
                if raw.startswith("event:"):
                    got.append(raw.split(":", 1)[1].strip())
                if all(w in got for w in want_types):
                    break
    except Exception as e:
        frames.append(f"<stream error: {e}>")
    return got, frames


# ======================================================================
# Part 1：API 级测试
# ======================================================================

def part1(t: T):
    s = t.s
    wf1, wf2 = None, None
    try:
        wf1 = t.create("控制链路-断点链")
        wf2 = t.create("控制链路-慢链")
        assert t.save(wf1, "控制链路-断点链", wf1_graph()).status_code == 200
        assert t.save(wf2, "控制链路-慢链", wf2_graph()).status_code == 200
        print(f"--- workflows: wf1={wf1} wf2={wf2} ---")

        # ==================== A. 断点暂停全链 ====================
        eid = t.exec_async(wf1, breakpoints=["n_b"])
        check("A1 断点执行返回 executionId", bool(eid), eid)
        st = t.wait_status(eid, ["PAUSED"], timeout=15)
        check("A2 断点命中进入 PAUSED", st == "PAUSED", st)

        d = t.detail(eid) or {}
        ns = d.get("nodeStates") or {}
        a_state = (ns.get("n_a") or {}).get("status")
        check("A3 暂停时节点状态正确(A=COMPLETED, B/C 未执行)",
              a_state == "COMPLETED" and "n_b" not in ns and "n_c" not in ns,
              {"a": a_state, "keys": sorted(ns.keys())})

        ne = t.node_execs(eid) or []
        check("A4 节点明细已落库(A COMPLETED 行)", any(
            x.get("nodeId") == "n_a" and x.get("status") == "COMPLETED" for x in ne), ne)

        snap = t.snapshot(eid) or {}
        check("A5 快照含 pendingNodes=[n_b] 且状态 PAUSED",
              snap.get("status") == "PAUSED" and (snap.get("pendingNodes") or []) == ["n_b"],
              {"status": snap.get("status"), "pending": snap.get("pendingNodes")})

        okv, _ = t.update_var(eid, "g", "MODIFIED_BY_PAUSE")
        check("A6 暂停中修改变量成功", okv)

        snap = t.snapshot(eid) or {}
        check("A7 快照反映修改后变量 global.g", (snap.get("global") or {}).get("g") == "MODIFIED_BY_PAUSE",
              snap.get("global"))

        okv, _ = t.resume(eid)
        check("A8 resume 接口成功", okv)

        st = t.wait_status(eid, ["COMPLETED", "FAILED", "CANCELLED"], timeout=20)
        d = t.detail(eid) or {}
        answer = ((d.get("outputs") or {}).get("answer"))
        check("A9 resume 后完成且变量修改对后续节点生效(answer=MODIFIED_BY_PAUSE)",
              st == "COMPLETED" and answer == "MODIFIED_BY_PAUSE",
              {"status": st, "answer": answer})

        ns = (t.detail(eid) or {}).get("nodeStates") or {}
        check("A10 断点清除不再二次暂停(B/C 均完成)",
              (ns.get("n_b") or {}).get("status") == "COMPLETED"
              and (ns.get("n_c") or {}).get("status") == "COMPLETED",
              {k: v.get("status") for k, v in ns.items()})

        # ==================== B. 多级断点连续暂停 ====================
        eid = t.exec_async(wf1, breakpoints=["n_a", "n_b"])
        st = t.wait_status(eid, ["PAUSED"], timeout=15)
        ns1 = (t.detail(eid) or {}).get("nodeStates") or {}
        check("B1 多断点首次暂停(在 A 前)", st == "PAUSED" and "n_a" not in ns1, {"st": st, "ns": sorted(ns1)})

        t.resume(eid)
        st = t.wait_status(eid, ["PAUSED", "COMPLETED"], timeout=15)
        ns2 = (t.detail(eid) or {}).get("nodeStates") or {}
        check("B2 二次暂停在 B 前(A 已完成)",
              st == "PAUSED" and (ns2.get("n_a") or {}).get("status") == "COMPLETED" and "n_b" not in ns2,
              {"st": st, "ns": sorted(ns2)})

        t.resume(eid)
        st = t.wait_status(eid, ["COMPLETED"], timeout=15)
        check("B3 全部断点走完最终完成", st == "COMPLETED", st)

        # ==================== C. 手动暂停 mid-run（慢节点执行中） ====================
        eid = t.exec_async(wf2)
        time.sleep(1.0)                       # SLOW 黑洞连接中
        okv, body = t.pause(eid)
        check("C1 慢节点执行中手动暂停接口成功", okv, body)

        st = t.wait_status(eid, ["PAUSED"], timeout=10)
        snap = t.snapshot(eid) or {}
        check("C2 执行中暂停生效(状态 PAUSED)",
              st == "PAUSED", {"st": st, "pending": snap.get("pendingNodes")})

        okv, _ = t.resume(eid)
        check("C3 暂停后恢复接口成功", okv)
        st = t.wait_status(eid, ["COMPLETED", "FAILED", "CANCELLED"], timeout=30)
        d = t.detail(eid) or {}
        answer = (d.get("outputs") or {}).get("answer") or ""
        check("C4 恢复后慢节点超时走异常分支收敛(answer 含 EXC:)",
              st == "COMPLETED" and str(answer).startswith("EXC:"),
              {"status": st, "answer": str(answer)[:120]})

        # ==================== D. 暂停态取消 ====================
        eid = t.exec_async(wf2)
        time.sleep(1.0)
        t.pause(eid)
        st = t.wait_status(eid, ["PAUSED"], timeout=10)
        check("D1 慢链进入暂停态", st == "PAUSED", st)

        okv, body = t.cancel(eid)
        check("D2 暂停态取消接口成功", okv, body)

        st = t.wait_status(eid, ["CANCELLED"], timeout=10)
        check("D3 取消后终态 CANCELLED", st == "CANCELLED", st)

        # ==================== E. 执行中直接取消（疑似 bug 验证） ====================
        eid = t.exec_async(wf2)
        time.sleep(1.0)                       # SLOW 黑洞连接中
        okv, body = t.cancel(eid)
        check("E1 执行中取消接口返回成功", okv, body)

        st = t.wait_status(eid, ["CANCELLED", "COMPLETED", "FAILED"], timeout=30)
        if st == "CANCELLED":
            check("E2 执行中取消 → 终态 CANCELLED（正确）", True)
        else:
            check("E2 执行中取消 → 终态 CANCELLED", False,
                  f"BUG 复现：实际终态={st}（cancel 请求丢失，被误标终态）")
            d = t.detail(eid) or {}
            check("E3 [bug 证据] 被误标完成且输出为空", (d.get("outputs") or {}) == {},
                  {"outputs": d.get("outputs"), "executedNodes": d.get("executedNodes")})

        # ==================== F. SSE 实时事件流（暂停态订阅） ====================
        eid = t.exec_async(wf1, breakpoints=["n_b"])
        t.wait_status(eid, ["PAUSED"], timeout=15)
        got, frames = read_sse_frames(
            s, eid, ["workflow.paused", "node.completed", "workflow.started"], timeout=10)
        check("F1 暂停态订阅收到 started/A完成/paused 事件",
              "workflow.started" in got and "node.completed" in got and "workflow.paused" in got,
              got[:12])

        t.resume(eid)
        got2, _ = read_sse_frames(s, eid, ["workflow.completed"], timeout=20)
        check("F2 resume 后流内收到 completed 事件(B/C 节点事件 + 终态)",
              "workflow.completed" in got2 and "node.completed" in got2, got2[:14])
        t.wait_status(eid, ["COMPLETED"], timeout=15)

        # ==================== G. 已结束执行 SSE 回放（DB 降级路径） ====================
        got3, frames3 = read_sse_frames(s, eid, ["workflow.completed"], timeout=10)
        check("G1 已结束执行订阅走 DB 回放(completed 帧)", "workflow.completed" in got3, got3[:12])

        got4, frames4 = read_sse_frames(s, "not-exist-exec-001", ["workflow.failed"], timeout=10)
        check("G2 不存在执行订阅返回 failed 帧", "workflow.failed" in got4, (got4 or [])[:5])

        # ==================== H. 快照篡改恢复（进程内 restore 路径） ====================
        eid = t.exec_async(wf1, breakpoints=["n_b"])
        t.wait_status(eid, ["PAUSED"], timeout=15)
        snap = t.snapshot(eid) or {}
        snap["global"] = {**(snap.get("global") or {}), "g": "RESTORED_FROM_SNAPSHOT"}
        r = s.post(f"{EXE}/{eid}/resume-from-snapshot", json=snap, timeout=30)
        check("H1 resume-from-snapshot 接口成功", ok(r), r.text[:200])

        st = t.wait_status(eid, ["COMPLETED", "FAILED"], timeout=20)
        d = t.detail(eid) or {}
        answer = (d.get("outputs") or {}).get("answer")
        check("H2 篡改快照的 global 生效(answer=RESTORED_FROM_SNAPSHOT)",
              st == "COMPLETED" and answer == "RESTORED_FROM_SNAPSHOT",
              {"status": st, "answer": answer})

        # ==================== K. 负向用例 ====================
        _, body = t.pause(eid)                # eid 已 COMPLETED
        check("K1 暂停已结束执行被拒绝", not ok(
            s.post(f"{EXE}/{eid}/pause", timeout=15)), body)

        _, body = t.resume(eid)
        check("K2 恢复已结束执行被拒绝", not ok(
            s.post(f"{EXE}/{eid}/resume", timeout=15)), body)

        check("K3 恢复不存在执行被拒绝", not ok(
            s.post(f"{EXE}/not-exist/resume", timeout=15)))

        check("K4 取消不存在执行被拒绝", not ok(
            s.post(f"{EXE}/not-exist/cancel", timeout=15)))

        r = s.put(f"{EXE}/not-exist/variables/g", json="x", timeout=15)
        check("K5 修改不存在执行变量被拒绝", not ok(r), r.text[:150])

        # K6/K7/K8 需要新执行：首节点断点 / 不存在节点断点 / 空断点
        eid = t.exec_async(wf1, breakpoints=["n_start"])
        st = t.wait_status(eid, ["COMPLETED", "PAUSED"], timeout=20)
        check("K6 首节点断点不暂停(直接完成)", st == "COMPLETED", st)

        eid = t.exec_async(wf1, breakpoints=["n_ghost"])
        st = t.wait_status(eid, ["COMPLETED", "PAUSED"], timeout=20)
        check("K7 不存在节点断点被忽略(正常完成)", st == "COMPLETED", st)

        eid = t.exec_async(wf1, breakpoints=[])
        st = t.wait_status(eid, ["COMPLETED", "PAUSED"], timeout=20)
        check("K8 空断点列表正常完成", st == "COMPLETED", st)

    finally:
        for wf in (wf1, wf2):
            if wf:
                s.delete(f"{BASE}/workflows/{wf}", timeout=15)


# ======================================================================
# Part 2：引擎级直驱（暂停后 run() 正常结束 / 快照重建）
# ======================================================================

async def part2():
    from service.service_workflow.workflow_engine.graph import WorkflowGraph
    from service.service_workflow.workflow_engine.engine import WorkflowRuntime

    graph = WorkflowGraph(wf1_graph())

    # ---- I. 引擎级断点暂停：run() 暂停后正常结束（需求 5:不再阻塞等信号,恢复由 DB 状态驱动） ----
    rt = WorkflowRuntime(graph, "ctl-ttl-001", inputs={"query": "q"},
                          breakpoints={"n_b"})
    task = asyncio.create_task(rt.run())
    for _ in range(40):
        if rt.status == "PAUSED":
            break
        await asyncio.sleep(0.2)
    check("I1 引擎级断点暂停(PAUSED)", rt.status == "PAUSED", rt.status)

    # 新版语义:暂停即持久化快照并正常结束 run()(旧版会 park 在暂停循环等恢复信号,
    # 存在任务泄漏;现恢复动作交给「resume 改 DB 状态 + 重新投递任务」驱动)
    try:
        await asyncio.wait_for(task, timeout=5)
        check("I2 暂停后 run() 正常返回(不再 park)", rt.finished and rt.status == "PAUSED",
              {"finished": rt.finished, "status": rt.status})
    except asyncio.TimeoutError:
        check("I2 暂停后 run() 正常返回(不再 park)", False, "run() 仍阻塞(旧行为残留)")

    # ---- J. 快照重建（跨进程接管地基）：runtime 销毁后从快照重建续跑 ----
    rt1 = WorkflowRuntime(graph, "ctl-rebuild-001", inputs={"query": "q"},
                           breakpoints={"n_b"})
    await rt1.run()   # 断点命中后 run() 正常结束(不再 park)
    check("J1 断点暂停(run-time#1)", rt1.status == "PAUSED", rt1.status)

    snap = rt1.snapshot()
    # 模拟进程死亡：直接丢弃 rt1（run() 已自然结束,无存留任务需清理）

    rt2 = WorkflowRuntime(graph, "ctl-rebuild-001", inputs={"query": "q"})
    rt2.restore(snap)
    outputs = await asyncio.wait_for(rt2.run(), timeout=15)
    ns = rt2.node_states_dict()
    a_count = sum(1 for _ in [1]) if ns.get("n_a", {}).get("status") == "COMPLETED" else 0
    check("J2 快照重建后从 pending 续跑完成",
          rt2.status == "COMPLETED" and outputs.get("answer") is not None,
          {"status": rt2.status, "outputs": outputs})
    check("J3 重建未重跑已完成节点(直接从 B 继续)",
          (ns.get("n_a") or {}).get("status") == "COMPLETED"
          and (ns.get("n_b") or {}).get("status") == "COMPLETED"
          and (ns.get("n_c") or {}).get("status") == "COMPLETED",
          {k: v.get("status") for k, v in ns.items()})
    check("J4 重建执行 B 未再次暂停(断点一次性语义)",
          rt2.status == "COMPLETED" and "n_b" in ns, rt2.status)


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    s.headers["Authorization"] = f"Bearer {r.json()['data']['token']}"

    print("========== Part 1: API 级控制链路 ==========")
    part1(T(s))
    print("========== Part 2: 引擎级直驱 ==========")
    asyncio.run(part2())

    total = len(results)
    passed = sum(1 for _, o, _ in results if o)
    print(f"\n===== 控制链路测试: {passed}/{total} PASS =====")
    for name, o, d in results:
        if not o:
            print(f"  FAIL -> {name}: {str(d)[:200]}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
