# -*- coding: utf-8 -*-
"""workflow 19 种节点类型逐一测试（经网关 18000）。

每个节点：创建工作流 → 保存图 → 校验 → 执行 → 断言输出/优雅失败 → 清理。
引擎图契约：节点配置在 node.data，位置 node.position，边 source/target/sourceHandle。
"""
import json
import sys
import time
import requests

BASE = "http://127.0.0.1:18000/api/workflow"
GW = "http://127.0.0.1:18000"
EXE = f"{GW}/api/workflow/workflow-executions"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + f" | {name}" + (f" | {str(detail)[:220]}" if not ok else ""))


def ok(resp):
    try:
        return resp.status_code == 200 and resp.json().get("code") == 200
    except Exception:
        return False


def pos(x, y):
    return {"x": x, "y": y}


def node(nid, ntype, label, x, y, data):
    return {"id": nid, "type": ntype, "label": label, "position": pos(x, y), "data": data}


def edge(eid, src, tgt, handle=None):
    e = {"id": eid, "source": src, "target": tgt}
    if handle:
        e["sourceHandle"] = handle
    return e


def start_node(fields):
    return node("n_start", "START", "开始", 100, 300, {"fields": fields})


def end_node(outputs):
    return node("n_end", "END", "结束", 900, 300, {"outputs": outputs})


def start_fields(query="你好世界", extra=None):
    fs = [{"name": "query", "label": "问题", "type": "INPUT", "required": True, "defaultValue": query}]
    if extra:
        fs.extend(extra)
    return fs


class T:
    def __init__(self, s):
        self.s = s

    def create(self, name):
        r = self.s.post(f"{BASE}/workflows", json={"name": name, "description": "节点测试"}, timeout=15)
        d = r.json().get("data")
        return d if isinstance(d, int) else (d or {}).get("id")

    def save(self, wf_id, name, graph):
        return self.s.put(f"{BASE}/workflows/{wf_id}", json={"name": name, "description": "节点测试", "graph": graph}, timeout=15)

    def validate(self, wf_id):
        r = self.s.get(f"{BASE}/workflows/{wf_id}/validate", timeout=15)
        if not ok(r):
            return None
        return r.json().get("data")

    def execute(self, wf_id, inputs, timeout=120):
        # 需求 4:同步 /execute 已取消,统一走 execute-async 投递 + 轮询执行记录直到终态
        r = self.s.post(f"{EXE}/workflows/{wf_id}/execute-async", json={"inputs": inputs}, timeout=30)
        if not ok(r):
            return {"_http": r.text[:200]}
        exe_id = r.json().get("data")
        if not exe_id:
            return {"_http": r.text[:200]}
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self.s.get(f"{EXE}/{exe_id}", timeout=15).json().get("data") or {}
            if str(d.get("status")) in ("COMPLETED", "FAILED", "CANCELLED"):
                return d
            time.sleep(0.5)
        return {"status": "TIMEOUT", "id": exe_id}

    def delete(self, wf_id):
        if wf_id:
            self.s.delete(f"{BASE}/workflows/{wf_id}", timeout=15)

    def run(self, name, graph, inputs=None, expect_status="COMPLETED"):
        """保存图→校验→执行，返回 (wf_id, issues, exe_data)"""
        wf_id = self.create(name)
        self.save(wf_id, name, graph)
        issues = self.validate(wf_id)
        exe = self.execute(wf_id, inputs if inputs is not None else {"query": "你好世界"})
        return wf_id, issues, exe

    def errors(self, issues):
        if not issues:
            return []
        if isinstance(issues, dict):
            issues = issues.get("issues") or issues.get("errors") or []
        return [i for i in issues if isinstance(i, dict) and str(i.get("severity", i.get("level", ""))).upper() in ("ERROR",)]


def exe_status(exe):
    return str(exe.get("status") or "")


def exe_outputs(exe):
    return exe.get("outputs") or {}


def main():
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{GW}/api/login/login", json={"username": "admin", "password": "Admin@123"}, timeout=15)
    token = r.json()["data"]["token"]
    s.headers["Authorization"] = f"Bearer {token}"
    t = T(s)

    # ============ 0. 节点定义完整性 ============
    r = s.get(f"{BASE}/workflows/node-definitions", timeout=15)
    defs = r.json().get("data") or []
    types = sorted(x.get("type") for x in defs if isinstance(x, dict))
    expect19 = sorted(["START", "END", "LLM", "QUESTION_CLASSIFIER", "PARAMETER_EXTRACTOR", "AGENT",
                       "IF_ELSE", "LOOP", "ITERATION", "PARALLEL", "VARIABLE_ASSIGNER",
                       "VARIABLE_AGGREGATOR", "TEMPLATE", "CODE", "LIST_OPERATOR", "DOC_EXTRACTOR",
                       "KNOWLEDGE_RETRIEVAL", "HTTP_REQUEST", "TOOL"])
    check("节点定义 19 种齐全", types == expect19, f"实际: {types}")

    # ============ 1. TEMPLATE（含 START/END 基础链路） ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_tpl", "TEMPLATE", "模板", 400, 300, {"template": "回声:{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "output"}),
                   end_node([{"name": "answer", "value": "{{n_tpl.text}}"}])],
         "edges": [edge("e1", "n_start", "n_tpl"), edge("e2", "n_tpl", "n_end")]}
    wf, issues, exe = t.run("节点测试-TEMPLATE", g, {"query": "你好世界"})
    check("TEMPLATE 校验无错", not t.errors(issues), issues)
    check("TEMPLATE 执行完成", exe_status(exe) == "COMPLETED", exe)
    check("TEMPLATE 输出正确", exe_outputs(exe).get("answer") == "回声:你好世界", exe_outputs(exe))
    t.delete(wf)

    # ============ 2. START 必填缺失（无 defaultValue）→ 优雅失败 ============
    g2 = {"nodes": [node("n_start", "START", "开始", 100, 300, {"fields": [{"name": "query", "label": "问题", "type": "INPUT", "required": True}]}),
                    node("n_tpl", "TEMPLATE", "模板", 400, 300, {"template": "{{n_start.query}}", "engine": "SIMPLE", "outputVariable": "output"}),
                    end_node([{"name": "answer", "value": "{{n_tpl.text}}"}])],
          "edges": [edge("e1", "n_start", "n_tpl"), edge("e2", "n_tpl", "n_end")]}
    wf, issues, exe = t.run("节点测试-START必填", g2, {})
    check("START 必填缺失执行失败(预期)", exe_status(exe) == "FAILED", exe)
    check("START 失败原因明确", "必填" in str(exe.get("error") or exe.get("errorMessage") or exe), exe)
    t.delete(wf)

    # ============ 3. IF_ELSE 双分支 ============
    g = {"nodes": [start_node(start_fields(query="5", extra=[{"name": "count", "label": "数量", "type": "INPUT", "required": False, "defaultValue": "5"}])),
                   node("n_if", "IF_ELSE", "条件", 350, 300, {"branches": [
                       {"id": "b1", "type": "IF", "label": "大于2", "operator": "AND",
                        "conditions": [{"variable": "n_start.count", "operator": "GREATER_THAN", "value": "2"}]},
                       {"id": "b2", "type": "ELSE", "label": "否则"}]}),
                   node("n_yes", "TEMPLATE", "是", 600, 150, {"template": "走IF分支:{{n_start.count}}", "engine": "SIMPLE", "outputVariable": "text"}),
                   node("n_no", "TEMPLATE", "否", 600, 450, {"template": "走ELSE分支:{{n_start.count}}", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "answer", "value": "{{n_yes.text}}{{n_no.text}}"}])],
         "edges": [edge("e1", "n_start", "n_if"),
                   edge("e2", "n_if", "n_yes", "branch:b1"),
                   edge("e3", "n_if", "n_no", "branch:b2"),
                   edge("e4", "n_yes", "n_end"), edge("e5", "n_no", "n_end")]}
    wf, issues, exe = t.run("节点测试-IF_ELSE", g, {"query": "q", "count": "5"})
    check("IF_ELSE 校验无错", not t.errors(issues), issues)
    check("IF_ELSE 走IF分支", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("answer") == "走IF分支:5", exe)
    exe = t.execute(wf, {"query": "q", "count": "1"})
    check("IF_ELSE 走ELSE分支", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("answer") == "走ELSE分支:1", exe)
    t.delete(wf)

    # ============ 4. VARIABLE_ASSIGNER ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_va", "VARIABLE_ASSIGNER", "赋值", 350, 300, {"assignments": [
                       {"variableName": "total", "type": "LITERAL", "value": "42", "variableType": "number"},
                       {"variableName": "echo", "type": "VARIABLE", "value": "n_start.query"}]}),
                   node("n_tpl", "TEMPLATE", "模板", 600, 300, {"template": "total={{global.total}} echo={{global.echo}}", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "answer", "value": "{{n_tpl.text}}"}])],
         "edges": [edge("e1", "n_start", "n_va"), edge("e2", "n_va", "n_tpl"), edge("e3", "n_tpl", "n_end")]}
    wf, issues, exe = t.run("节点测试-VAR_ASSIGNER", g)
    check("VARIABLE_ASSIGNER 赋值生效", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("answer") == "total=42 echo=你好世界", exe)
    t.delete(wf)

    # ============ 5. VARIABLE_AGGREGATOR ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_a", "TEMPLATE", "A", 350, 150, {"template": "甲", "engine": "SIMPLE", "outputVariable": "text"}),
                   node("n_b", "TEMPLATE", "B", 350, 450, {"template": "乙", "engine": "SIMPLE", "outputVariable": "text"}),
                   node("n_agg", "VARIABLE_AGGREGATOR", "聚合", 600, 300, {"groups": [
                       {"outputVariable": "picked", "sourceVariables": ["n_a.text", "n_b.text"], "strategy": "MERGE_TO_ARRAY"}]}),
                   end_node([{"name": "answer", "value": "{{n_agg.picked}}"}])],
         "edges": [edge("e1", "n_start", "n_a"), edge("e2", "n_start", "n_b"),
                   edge("e3", "n_a", "n_agg"), edge("e4", "n_b", "n_agg"), edge("e5", "n_agg", "n_end")]}
    wf, issues, exe = t.run("节点测试-VAR_AGGREGATOR", g)
    picked = exe_outputs(exe).get("answer")
    check("VARIABLE_AGGREGATOR 聚合为数组", exe_status(exe) == "COMPLETED" and picked == ["甲", "乙"], exe_outputs(exe))
    t.delete(wf)

    # ============ 6. CODE ============
    code = "def main(q):\n    return {'len': len(q), 'upper': q.upper()}"
    g = {"nodes": [start_node(start_fields()),
                   node("n_code", "CODE", "代码", 400, 300, {"language": "PYTHON", "code": code,
                       "inputs": [{"name": "q", "sourceVariable": "n_start.query"}], "outputVariable": "result"}),
                   end_node([{"name": "len", "value": "{{n_code.len}}"}, {"name": "upper", "value": "{{n_code.upper}}"}])],
         "edges": [edge("e1", "n_start", "n_code"), edge("e2", "n_code", "n_end")]}
    wf, issues, exe = t.run("节点测试-CODE", g, {"query": "abc"})
    check("CODE 校验无错", not t.errors(issues), issues)
    check("CODE 执行输出", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("len") == 3 and exe_outputs(exe).get("upper") == "ABC", exe_outputs(exe))
    # CODE 沙箱：禁 import
    g["nodes"][1]["data"]["code"] = "def main(q):\n    import os\n    return {'x': 1}"
    t.save(wf, "节点测试-CODE", g)
    exe = t.execute(wf, {"query": "abc"})
    check("CODE 沙箱禁import(预期失败)", exe_status(exe) == "FAILED", exe)
    t.delete(wf)

    # ============ 7. LIST_OPERATOR ============
    g = {"nodes": [start_node(start_fields(extra=[{"name": "items", "label": "列表", "type": "INPUT", "required": False, "defaultValue": ["甲", "乙", "丙"]}])),
                   node("n_lo", "LIST_OPERATOR", "列表操作", 400, 300, {"inputVariable": "n_start.items", "operationType": "FIRST", "outputVariable": "first"}),
                   end_node([{"name": "first", "value": "{{n_lo.first}}"}])],
         "edges": [edge("e1", "n_start", "n_lo"), edge("e2", "n_lo", "n_end")]}
    wf, issues, exe = t.run("节点测试-LIST_OP", g, {"query": "q", "items": ["甲", "乙", "丙"]})
    check("LIST_OPERATOR FIRST", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("first") == "甲", exe_outputs(exe))
    # SLICE
    g["nodes"][1]["data"] = {"inputVariable": "n_start.items", "operationType": "SLICE", "outputVariable": "sliced", "sliceConfig": {"start": 1, "end": 3}}
    g["nodes"][2]["data"] = {"outputs": [{"name": "s", "value": "{{n_lo.sliced}}"}]}
    t.save(wf, "节点测试-LIST_OP", {"nodes": g["nodes"], "edges": g["edges"]})
    exe = t.execute(wf, {"query": "q", "items": ["甲", "乙", "丙", "丁"]})
    check("LIST_OPERATOR SLICE", exe_status(exe) == "COMPLETED" and exe_outputs(exe).get("s") == ["乙", "丙"], exe_outputs(exe))
    t.delete(wf)

    # ============ 8. LOOP ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_loop", "LOOP", "循环", 350, 300, {"loopVariable": "i", "maxIterations": 10, "exitCondition": "{{global.i}} >= 3", "outputVariable": "loopResult"}),
                   node("n_body", "TEMPLATE", "循环体", 600, 300, {"template": "第{{global.i}}轮", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "iterations", "value": "{{n_loop.iterations}}"}])],
         "edges": [edge("e1", "n_start", "n_loop"),
                   edge("e2", "n_loop", "n_body", "branch:body"),
                   edge("e3", "n_loop", "n_end")]}
    wf, issues, exe = t.run("节点测试-LOOP", g)
    check("LOOP 校验无错", not t.errors(issues), issues)
    check("LOOP 迭代3次退出", exe_status(exe) == "COMPLETED" and str(exe_outputs(exe).get("iterations")) == "3", exe_outputs(exe))
    t.delete(wf)

    # ============ 9. ITERATION ============
    g = {"nodes": [start_node(start_fields(extra=[{"name": "items", "label": "列表", "type": "INPUT", "required": False, "defaultValue": ["a", "b", "c"]}])),
                   node("n_iter", "ITERATION", "迭代", 350, 300, {"arrayVariable": "n_start.items", "processingMode": "SEQUENTIAL", "outputVariable": "items"}),
                   node("n_body", "TEMPLATE", "迭代体", 600, 300, {"template": "{{item}}-{{index}}", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "items", "value": "{{n_iter.items}}"}])],
         "edges": [edge("e1", "n_start", "n_iter"),
                   edge("e2", "n_iter", "n_body", "branch:body"),
                   edge("e3", "n_iter", "n_end")]}
    wf, issues, exe = t.run("节点测试-ITERATION", g, {"query": "q", "items": ["a", "b", "c"]})
    check("ITERATION 校验无错", not t.errors(issues), issues)
    items = exe_outputs(exe).get("items")
    okk = exe_status(exe) == "COMPLETED" and isinstance(items, list) and len(items) == 3
    check("ITERATION 逐元素执行", okk, exe_outputs(exe))
    t.delete(wf)

    # ============ 10. PARALLEL ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_par", "PARALLEL", "并行", 350, 300, {"branches": [{"id": "p1", "name": "分支1"}, {"id": "p2", "name": "分支2"}], "waitStrategy": "ALL"}),
                   node("n_a", "TEMPLATE", "A", 600, 150, {"template": "分支A完成", "engine": "SIMPLE", "outputVariable": "text"}),
                   node("n_b", "TEMPLATE", "B", 600, 450, {"template": "分支B完成", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "answer", "value": "{{n_par.branches}}"}])],
         "edges": [edge("e1", "n_start", "n_par"),
                   edge("e2", "n_par", "n_a", "branch:p1"),
                   edge("e3", "n_par", "n_b", "branch:p2"),
                   edge("e4", "n_par", "n_end")]}
    wf, issues, exe = t.run("节点测试-PARALLEL", g)
    branches = exe_outputs(exe).get("answer")
    check("PARALLEL 校验无错", not t.errors(issues), issues)
    check("PARALLEL 双分支完成", exe_status(exe) == "COMPLETED" and isinstance(branches, (dict, str)) and str(branches).find("p1") >= 0 and str(branches).find("p2") >= 0, exe_outputs(exe))
    t.delete(wf)

    # ============ 11. HTTP_REQUEST ============
    # 11a. 200 响应：POST 登录接口（无需鉴权）
    g = {"nodes": [start_node(start_fields()),
                   node("n_http", "HTTP_REQUEST", "HTTP", 400, 300, {"url": "http://127.0.0.1:18000/api/login/login", "method": "POST", "bodyType": "JSON", "body": {"username": "admin", "password": "Admin@123"}, "outputVariable": "response", "parseJsonResponse": True}),
                   end_node([{"name": "answer", "value": "{{n_http.response}}"}])],
         "edges": [edge("e1", "n_start", "n_http"), edge("e2", "n_http", "n_end")]}
    wf, issues, exe = t.run("节点测试-HTTP_REQUEST", g)
    check("HTTP_REQUEST 校验无错", not t.errors(issues), issues)
    check("HTTP_REQUEST 200响应捕获", exe_status(exe) == "COMPLETED" and "token" in json.dumps(exe_outputs(exe), ensure_ascii=False), exe_outputs(exe))
    # 11b. 403 响应默认失败
    g["nodes"][1]["data"] = {"url": "http://127.0.0.1:18000/api/workflow/workflows/node-definitions", "method": "GET", "outputVariable": "response"}
    t.save(wf, "节点测试-HTTP_REQUEST", {"nodes": g["nodes"], "edges": g["edges"]})
    exe = t.execute(wf, {"query": "q"})
    check("HTTP_REQUEST 4xx默认失败", exe_status(exe) == "FAILED", exe)
    # 11c. failOnError=false 时错误响应作为输出
    g["nodes"][1]["data"]["failOnError"] = False
    t.save(wf, "节点测试-HTTP_REQUEST", {"nodes": g["nodes"], "edges": g["edges"]})
    exe = t.execute(wf, {"query": "q"})
    check("HTTP_REQUEST failOnError=false放行", exe_status(exe) == "COMPLETED" and "403" in json.dumps(exe_outputs(exe)), exe_outputs(exe))
    t.delete(wf)

    # ============ 12-15. AI 节点：校验通过 + 执行优雅失败 ============
    # LLM
    g = {"nodes": [start_node(start_fields()),
                   node("n_llm", "LLM", "大模型", 400, 300, {"modelId": "test-model", "promptTemplate": "回答:{{n_start.query}}", "outputVariable": "output"}),
                   end_node([{"name": "answer", "value": "{{n_llm.output}}"}])],
         "edges": [edge("e1", "n_start", "n_llm"), edge("e2", "n_llm", "n_end")]}
    wf, issues, exe = t.run("节点测试-LLM", g)
    check("LLM 配置完整校验通过", not t.errors(issues), issues)
    check("LLM 执行优雅失败(模型不可用)", exe_status(exe) == "FAILED", exe)
    t.delete(wf)
    # LLM 缺 modelId → 校验报错
    g["nodes"][1]["data"]["modelId"] = ""
    wf = t.create("节点测试-LLM缺模型")
    t.save(wf, "节点测试-LLM缺模型", g)
    issues = t.validate(wf)
    check("LLM 缺modelId校验拦截", bool(t.errors(issues)), issues)
    t.delete(wf)

    # QUESTION_CLASSIFIER
    g = {"nodes": [start_node(start_fields()),
                   node("n_qc", "QUESTION_CLASSIFIER", "分类", 400, 300, {"modelId": "test-model", "categories": [{"id": "c1", "name": "技术"}, {"id": "c2", "name": "闲聊"}], "outputVariable": "category"}),
                   node("n_t1", "TEMPLATE", "技术", 650, 150, {"template": "技术类", "engine": "SIMPLE", "outputVariable": "text"}),
                   node("n_t2", "TEMPLATE", "闲聊", 650, 450, {"template": "闲聊类", "engine": "SIMPLE", "outputVariable": "text"}),
                   end_node([{"name": "answer", "value": "{{n_t1.text}}{{n_t2.text}}"}])],
         "edges": [edge("e1", "n_start", "n_qc"),
                   edge("e2", "n_qc", "n_t1", "branch:c1"), edge("e3", "n_qc", "n_t2", "branch:c2"),
                   edge("e4", "n_t1", "n_end"), edge("e5", "n_t2", "n_end")]}
    wf, issues, exe = t.run("节点测试-分类器", g, {"query": "电脑蓝屏怎么办"})
    check("QUESTION_CLASSIFIER 校验通过", not t.errors(issues), issues)
    st = exe_status(exe)
    if st == "COMPLETED":
        check("QUESTION_CLASSIFIER 分类执行(降级匹配)", "技术" in str(exe_outputs(exe)), exe_outputs(exe))
    else:
        check("QUESTION_CLASSIFIER 执行优雅失败", st == "FAILED", exe)
    t.delete(wf)

    # PARAMETER_EXTRACTOR
    g = {"nodes": [start_node(start_fields()),
                   node("n_pe", "PARAMETER_EXTRACTOR", "参数提取", 400, 300, {"modelId": "test-model", "inputVariable": "n_start.query", "parameters": [{"name": "city", "type": "string", "description": "城市", "required": True}], "outputVariable": "params"}),
                   end_node([{"name": "city", "value": "{{n_pe.city}}"}])],
         "edges": [edge("e1", "n_start", "n_pe"), edge("e2", "n_pe", "n_end")]}
    wf, issues, exe = t.run("节点测试-参数提取", g)
    check("PARAMETER_EXTRACTOR 校验通过", not t.errors(issues), issues)
    check("PARAMETER_EXTRACTOR 执行优雅失败", exe_status(exe) == "FAILED", exe)
    t.delete(wf)

    # AGENT
    g = {"nodes": [start_node(start_fields()),
                   node("n_agent", "AGENT", "智能体", 400, 300, {"agentId": 1, "outputVariable": "output"}),
                   end_node([{"name": "answer", "value": "{{n_agent.output}}"}])],
         "edges": [edge("e1", "n_start", "n_agent"), edge("e2", "n_agent", "n_end")]}
    wf, issues, exe = t.run("节点测试-AGENT", g)
    check("AGENT 校验通过", not t.errors(issues), issues)
    st = exe_status(exe)
    check("AGENT 执行(完成或优雅失败)", st in ("COMPLETED", "FAILED"), exe)
    t.delete(wf)
    # AGENT 缺 agentId 校验
    g["nodes"][1]["data"]["agentId"] = None
    wf = t.create("节点测试-AGENT缺ID")
    t.save(wf, "节点测试-AGENT缺ID", g)
    issues = t.validate(wf)
    check("AGENT 缺agentId校验拦截", bool(t.errors(issues)), issues)
    t.delete(wf)

    # ============ 16. KNOWLEDGE_RETRIEVAL ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_kb", "KNOWLEDGE_RETRIEVAL", "知识检索", 400, 300, {"knowledgeBaseIds": [1], "queryVariable": "n_start.query", "topK": 3, "outputVariable": "docs"}),
                   end_node([{"name": "docs", "value": "{{n_kb.docs}}"}])],
         "edges": [edge("e1", "n_start", "n_kb"), edge("e2", "n_kb", "n_end")]}
    wf, issues, exe = t.run("节点测试-知识检索", g)
    check("KNOWLEDGE_RETRIEVAL 校验通过", not t.errors(issues), issues)
    check("KNOWLEDGE_RETRIEVAL 执行(完成或优雅失败)", exe_status(exe) in ("COMPLETED", "FAILED"), exe)
    t.delete(wf)

    # ============ 17. TOOL ============
    g = {"nodes": [start_node(start_fields()),
                   node("n_tool", "TOOL", "工具", 400, 300, {"toolName": "web_search", "toolParams": {"q": "{{n_start.query}}"}, "outputVariable": "output"}),
                   end_node([{"name": "answer", "value": "{{n_tool.output}}"}])],
         "edges": [edge("e1", "n_start", "n_tool"), edge("e2", "n_tool", "n_end")]}
    wf, issues, exe = t.run("节点测试-TOOL", g)
    check("TOOL 校验通过", not t.errors(issues), issues)
    check("TOOL 执行(完成或优雅失败)", exe_status(exe) in ("COMPLETED", "FAILED"), exe)
    t.delete(wf)

    # ============ 18. DOC_EXTRACTOR ============
    g = {"nodes": [start_node(start_fields(extra=[{"name": "doc", "label": "文档", "type": "INPUT", "required": False, "defaultValue": "测试.txt"}])),
                   node("n_doc", "DOC_EXTRACTOR", "文档提取", 400, 300, {"fileVariable": "n_start.doc", "outputVariable": "content"}),
                   end_node([{"name": "content", "value": "{{n_doc.content}}"}])],
         "edges": [edge("e1", "n_start", "n_doc"), edge("e2", "n_doc", "n_end")]}
    wf, issues, exe = t.run("节点测试-DOC提取", g, {"query": "q", "doc": "测试.txt"})
    check("DOC_EXTRACTOR 校验通过", not t.errors(issues), issues)
    check("DOC_EXTRACTOR 执行(完成或优雅失败)", exe_status(exe) in ("COMPLETED", "FAILED"), exe)
    t.delete(wf)

    # ============ 19. 负向校验：LOOP/ITERATION/PARALLEL/CODE 缺配置 ============
    neg_cases = [
        ("LOOP无退出条件", {"nodes": [start_node(start_fields()), node("n_loop", "LOOP", "循环", 350, 300, {}), end_node([])],
                          "edges": [edge("e1", "n_start", "n_loop"), edge("e2", "n_loop", "n_end")]}, "LOOP_NO_EXIT"),
        ("ITERATION无数组", {"nodes": [start_node(start_fields()), node("n_iter", "ITERATION", "迭代", 350, 300, {}), end_node([])],
                             "edges": [edge("e1", "n_start", "n_iter"), edge("e2", "n_iter", "n_end")]}, "ITER_NO_ARRAY"),
        ("CODE空代码", {"nodes": [start_node(start_fields()), node("n_code", "CODE", "代码", 400, 300, {"code": ""}), end_node([])],
                       "edges": [edge("e1", "n_start", "n_code"), edge("e2", "n_code", "n_end")]}, "CODE_EMPTY"),
        ("PARALLEL无分支", {"nodes": [start_node(start_fields()), node("n_par", "PARALLEL", "并行", 350, 300, {}), end_node([])],
                            "edges": [edge("e1", "n_start", "n_par"), edge("e2", "n_par", "n_end")]}, "PAR_NO_BRANCH"),
    ]
    for name, graph, code_expect in neg_cases:
        wf = t.create(f"节点测试-负向-{name}")
        t.save(wf, f"节点测试-负向-{name}", graph)
        issues = t.validate(wf)
        found = any(code_expect in str(i.get("code") or i) for i in (issues if isinstance(issues, list) else (issues or {}).get("issues", [])))
        check(f"负向校验:{name}", found, issues)
        t.delete(wf)

    print("\n===== 汇总 =====")
    passed = sum(1 for _, o, _ in results if o)
    print(f"{passed}/{len(results)} PASS")
    fails = [(n, d) for n, o, d in results if not o]
    if fails:
        print("失败项:")
        for n, d in fails:
            print(f"  - {n}: {str(d)[:200]}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
