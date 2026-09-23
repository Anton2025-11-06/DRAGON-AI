# -*- coding: utf-8 -*-
"""APPROVAL 人工审批节点：在节点边界挂起，等 submit 带回结论后续跑。

设计口径（docs/workflow-execution-contract.md）：
- 暂停范围 `pauseScope`：DOWNSTREAM（默认，仅本节点及下游等待）/ ALL（整条流一起停）；
- 职责：**只收集审批结论，不参与工作流流转控制**。出口为**单出口**，同意与不同意都照常
  路由下游（要按结论走不同分支，下游接一个条件节点引用 `review` 即可）；
- 输出：透传给下游的输入数据（默认回溯**全部**上游，可按 `passThroughInputs` 只放行
  选中的参数，支持到子字段粒度）+ `review`(bool) + `reviewOpinion` + `reviewBy`；
- 审批人识别：审批节点**配了审批人**时，开始节点才需要一个 `type=APPROVER` 的入参字段
  作为提交时的身份来源，比对时该入参（数组）与审批节点 `approvers` 求交集；
  审批人全部留空 → 不校验身份（持 api-key 且知道 executionId 者皆可审）；
  嵌套调用时这份身份**跟着父流往下传**（口径见 `seed_approver_identity`），且审批面板里
  改了那一行就以改后的为准（口径见 `lift_approver_edits`）；
- 编辑回写：同意时对 `(源节点id, 变量名, 子路径, 新值)` 四元组里的值做修改，回写源节点输出。
  不同意不应用编辑（结论已否决，改数据没有意义，表单里的改动随本轮丢弃）。
  可编辑清单与透传输出同源：放行什么就能改什么（只有上游审批的结论键只放行不改，
  开始入参也在可编辑之列 —— 父流等子流程审批时，子流的开始入参就是要审的那份数据）。

本模块同时承载「审批人字段」的读取口径：提交接口（service 层）与节点执行器共用，
避免两处各写一份 START 字段解析。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from service.service_workflow.workflow_engine.context import (
    ExecutionContext, _dig, split_path)
from service.service_workflow.workflow_engine.nodes.base import (
    APPROVAL_SCOPE_ALL, APPROVAL_SCOPE_DOWNSTREAM,
    AwaitingApproval, BaseNodeExecutor, NodeResult, issue,
)

# START 输入字段的「审批入参」类型标记（与前端 types.ts InputField.type 一致）
APPROVER_FIELD_TYPE = "APPROVER"

# 透传后的输出键名：必须能当 {{nodes.<审批>.<键>}} 的单段用，带点/方括号会被
# 当成子路径切分（存进去的键与下游引用的路径就对不上）
PASS_THROUGH_NAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5-]+$")

# 审批自己产出、不允许下游审批人改写的结论键（那是别家审批的审计结果）
CONCLUSION_KEYS = frozenset({"review", "reviewOpinion", "reviewBy"})


def normalize_approvers(value: Any) -> list:
    """审批人标识归一：单值/数组统一成去空后的字符串可比集合（元素 number|string 混填）。"""
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return [i for i in (str(x).strip() for x in items if x is not None and str(x) != "") if i]


def find_approver_field(graph) -> Optional[dict]:
    """取 START 节点上 type=APPROVER 的输入字段定义（没有则 None）。"""
    start = graph.find_start_node() if graph is not None else None
    if start is None:
        return None
    for f in (start.data or {}).get("fields") or []:
        if str(f.get("type") or "").upper() == APPROVER_FIELD_TYPE:
            return f
    return None


def approver_field_name(graph) -> str:
    """开始节点上「审批入参」字段的变量名；图里没有这个字段则空串。"""
    return str((find_approver_field(graph) or {}).get("name") or "")


def clean_approver_list(value: Any) -> list:
    """审批人值 → 数组：丢掉空元素并按原值去重，**保留元素原类型**。

    与 `normalize_approvers` 的差别只在不归一成字符串：写回 inputs/节点输出时不该把
    数字 id 变成字符串（下游可能有别的用法），而校验比对时两边本来就都会 str 化。
    """
    raw = (list(value) if isinstance(value, (list, tuple, set))
           else [] if value is None or value == "" else [value])
    seen, out = set(), []
    for item in raw:
        key = "" if item is None else str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def seed_approver_identity(graph, inputs: dict, identity: Any) -> dict:
    """父流调起子执行时，把父流自己的审批人身份补进子流的开始入参（原地不改）。

    子流那道审批的身份校验读的是**子执行那行 inputs**，而父流传进去的入参是模型或
    入参绑定决定的，没人会在里面填审批人工号 —— 不补的话，只要子流配了审批人，
    父侧一提交就是「无审批权限」，而页面上用户根本无从下手。

    - 子流开始节点没有「审批入参」字段 → 原样返回（这份身份对它没意义）；
    - 调用方已显式带了该字段 → 不覆盖（绑定里写明了就按写明的）；
    - 父流自己那份是空 → 也不写。**刻意不回落到登录用户/api-key 归属人**：直接走
      API 调用的父流可能压根没有登录态，凭空造一个身份等于把子流的权限校验变成走过场。
    """
    name = approver_field_name(graph)
    if not name or normalize_approvers((inputs or {}).get(name)):
        return inputs
    wanted = clean_approver_list(identity)
    if not wanted:
        return inputs
    merged = dict(inputs or {})
    merged[name] = wanted
    return merged


def lift_approver_edits(graph, inputs: dict, edits) -> dict:
    """本轮编辑里改了开始节点的「审批入参」→ 同步抬进 inputs 参与身份校验（原地不改）。

    校验发生在引擎跑起来之前，读的是 inputs；而面板的编辑只回写
    `node_states[开始].output`（要等本轮跑起来才生效）：不把这一行抬过去，用户在审批
    面板里填的审批人就永远不算数。身份不是普通的待审数据，它是「这一轮谁能提交」的
    依据，所以这一项按例外处理。edits 收 pydantic 结论项与 dict 两种形态。
    """
    if graph is None:
        return inputs
    name = approver_field_name(graph)
    start = graph.find_start_node() if name else None
    if start is None:
        return inputs
    merged = dict(inputs or {})
    for edit in edits or []:
        if isinstance(edit, dict):
            node_id, var = edit.get("nodeId"), edit.get("varName")
            path, value = edit.get("path"), edit.get("value")
        else:
            node_id = getattr(edit, "nodeId", None)
            var = getattr(edit, "varName", None)
            path = getattr(edit, "path", None)
            value = getattr(edit, "value", None)
        if node_id == start.id and var == name and not str(path or "").strip():
            merged[name] = clean_approver_list(value)
    return merged


def approval_nodes(graph) -> list:
    """图内全部审批节点（按画布顺序）。"""
    return [n for n in graph.nodes if n.type == "APPROVAL"] if graph is not None else []


def pass_through_selection(cfg: dict) -> list:
    """`passThroughInputs` 归一：[{nodeId,varName,path,name}] → [(node_id, var, path, out_name)]。

    空列表 = 未配置（全部上游透传），与「配置了但一项都不剩」区分不开：后者按未配置处理，
    免得删空参数的图突然把下游数据全掐掉。
    输出键名缺省取子路径末段（无路径则用变量名），纯数字的段用「变量名_下标」免得
    下游要写 `{{nodes.app.0}}` 这种看不出来源的引用。
    """
    items = []
    for raw in (cfg or {}).get("passThroughInputs") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("nodeId") or "")
        var = str(raw.get("varName") or "")
        path = str(raw.get("path") or "").strip()
        name = str(raw.get("name") or "").strip() or default_pass_through_name(var, path)
        if node_id and var and name:
            items.append((node_id, var, path, name))
    return items


def default_pass_through_name(var: str, path: str) -> str:
    """透传输出键名的缺省值：子路径末段（数组下标则带上变量名做前缀）。"""
    tokens = split_path(path)
    if not tokens:
        return var
    last = tokens[-1]
    return f"{var}_{last}" if last.isdigit() else last


def dig_output(output: Any, var: str, path: str) -> tuple[Any, bool]:
    """从节点输出里按 (变量名, 子路径) 取值：返回 (值, 是否取到)。

    变量名按 `in` 判定，所以值真的是 null 也算取到（透传下去下游拿到 None 是有意义的）；
    子路径只能靠 `_dig` 的结果判断，取不到与值本身为 null 分不开，统一按「没这个键」
    不占位：下游引用不存在的键本来就得到 None，而把一个陈旧的键名留在输出里反而
    让下游以为透传成功了。
    """
    if not isinstance(output, dict) or var not in output:
        return None, False
    if not path:
        return output[var], True
    value = _dig(output[var], path)
    return (value, True) if value is not None else (None, False)


def upstream_layers(graph, node_id: str) -> list:
    """反向回溯上游，按「距本节点的跳数」分层：第 0 层 = 直接入边来源（保持入边顺序）。

    审批节点是引用屏障：它计入上游，但不继续往它上游回溯 —— 隔着它的原始数据必须先从
    那道审批的输出处拿（它已把放行的那份铺进自己输出），否则等于绕开审批人的编辑与结论。
    """
    layers = []
    visited = {node_id}
    current = [e.source for e in graph.get_in_edges(node_id) if e.source]
    while current:
        layer = []
        for nid in current:
            if nid not in visited:
                visited.add(nid)
                layer.append(nid)
        if layer:
            layers.append(layer)
        nxt = []
        for nid in layer:
            node = graph.get_node(nid)
            if node is None or node.type == "APPROVAL":
                continue
            nxt.extend(e.source for e in graph.get_in_edges(nid) if e.source)
        current = nxt
    return layers


def verify_approver(graph, inputs: dict, nodes: Optional[list] = None) -> tuple[bool, Optional[str]]:
    """审批权限校验（用户决策 ⑧）。

    :return: (是否有权, 审批人标识 reviewBy)

    - 审批节点 `approvers` 全部留空 → 不校验（持 api-key 且知道 executionId 者皆可）；
    - START 无审批入参字段、或本次 inputs 未带 → 无权（提示先补参数）；
    - reviewBy 取审批入参首个值，缺省由调用方回落登录用户。
    """
    nodes = nodes if nodes is not None else approval_nodes(graph)
    allowed: list = []
    for n in nodes:
        allowed.extend(normalize_approvers((n.data or {}).get("approvers")))
    if not allowed:
        return True, None
    field = find_approver_field(graph)
    if not field or not field.get("name"):
        return False, None
    given = normalize_approvers((inputs or {}).get(field["name"]))
    if not given:
        return False, None
    if not set(given) & set(allowed):
        return False, None
    return True, given[0]


class ApprovalNodeExecutor(BaseNodeExecutor):
    """人工审批节点。"""

    node_type = "APPROVAL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        node_id = self.node.id
        scope = self._scope(cfg)
        # 本轮结论：取出即消费（同一提交重复执行到该节点时不应再命中）
        decision = self.runtime.approval_decisions.pop(node_id, None)
        if decision is None:
            # 没有结论 → 请求引擎在节点边界落暂停。不在此 await 等审批：
            # 那会占住 worker 任务槽并受节点超时约束，一晚上不点同意就把节点判失败。
            raise AwaitingApproval(node_id, self._approval_context(ctx), scope)

        approved = bool(decision.get("approved"))
        opinion = str(decision.get("opinion") or "")
        review_by = str(decision.get("reviewBy") or "") or None
        diff: list = []
        if approved:
            # 同意：先回写编辑（改源节点 output + ctx），再取透传 → 拿到的是编辑后的值
            diff = await self.runtime.apply_output_edits(node_id, decision.get("edits") or [])
        # 不同意什么都不做：审批只负责把结论记下来，要不要换个走法是下游条件节点自己的事

        output = self._output(ctx, approved=approved, opinion=opinion,
                              review_by=review_by)
        state = self.runtime.node_states.get(node_id)
        if state is not None:
            # 审批审计随 node_states 落库（恢复轮次与执行详情都读这里）
            state.review = approved
            state.reviewBy = review_by
            state.reviewOpinion = opinion
            state.reviewDiff = diff
        return NodeResult(output=output)

    # ---------- 配置与上下文 ----------

    @staticmethod
    def _scope(cfg: dict) -> str:
        raw = str(cfg.get("pauseScope") or "").upper()
        return (APPROVAL_SCOPE_ALL if raw == APPROVAL_SCOPE_ALL
                else APPROVAL_SCOPE_DOWNSTREAM)

    def _output(self, ctx: ExecutionContext, *, approved: bool,
                opinion: str = "", review_by: Optional[str] = None) -> dict:
        """审批输出 = 透传参数 + 人工结论三键。

        结论键在透传之后写入：上游刚好有个同名变量时以本节点为准，否则
        `{{nodes.<审批>.review}}` 会随机拿到上游的值。拿到结论才会走到这里（没结论
        时上面已经抛了 AwaitingApproval），所以不会伪造出一个「像同意」的假值。
        结论只是记录、不是控制：同意与否都照原样往下游交。
        """
        output = {name: entry["value"]
                  for name, entry in self._resolve_pass_through(ctx).items()}
        output["review"] = approved
        output["reviewOpinion"] = opinion or ""
        output["reviewBy"] = review_by or ""
        return output

    def _resolve_pass_through(self, ctx: ExecutionContext) -> dict:
        """审批要放行给下游的输入数据：{输出键名: {nodeId, varName, path, value}}。

        前端 VariableSelector 镜像同一份合并口径，两边不一致就会出现「下拉里选得到、
        运行期取不到」的契约漂移：

        - 未配 `passThroughInputs`：由近到远回溯全部上游（隔着普通节点继续往上，上游审批
          是屏障），同名键以先出现的那份（更接近本节点的）为准，开始入参铺最底层兜底。
          键名 = 上游自己的变量名（整值透传，无子路径）；
        - 配了 `passThroughInputs`：只按选中的 (节点, 变量, 子路径) 对取值，输出键名用
          配置里的 `name`（缺省取路径末段），按选择顺序先选先胜；取不到就不占位。

        必须在 `apply_output_edits` 之后调用：拿到的要是审批人改过的值。
        """
        graph = getattr(ctx, "graph", None)
        if graph is None:
            return {}
        resolved: dict = {}
        selection = pass_through_selection(self.config)
        if selection:
            for node_id, var, path, name in selection:
                value, ok = dig_output(ctx.get_node_output(node_id), var, path)
                if ok:
                    resolved.setdefault(name, {"nodeId": node_id, "varName": var,
                                               "path": path, "value": value})
            return resolved
        for layer in upstream_layers(graph, self.node.id):
            for node_id in layer:
                output = ctx.get_node_output(node_id)
                if not isinstance(output, dict):
                    continue
                for var, value in output.items():
                    resolved.setdefault(var, {"nodeId": node_id, "varName": var,
                                              "path": "", "value": value})
        start = graph.find_start_node()
        if start is not None:
            output = ctx.get_node_output(start.id)
            if isinstance(output, dict):
                for var, value in output.items():
                    resolved.setdefault(var, {"nodeId": start.id, "varName": var,
                                              "path": "", "value": value})
        return resolved

    def _editable_items(self, ctx: ExecutionContext) -> list:
        """外发给审批方的可编辑清单：与透传输出同源，但剔除不该被改的值。

        - 开始入参**照常放行**（它与透传集同源，能放给下游就能改）：审批人要核对、
          甚至当场改掉的往往就是上游传进来的那份值 —— 尤其是父流调起一条带审批的子
          工作流时，子流的开始入参就是父流传过去的那批参数，剔掉它父侧就只能看到一个
          空表单。回写只落在本轮 node_states[开始节点].output，RETRY 会被外部新入参
          覆写（那本来是一次全新执行），不会污染存量。
        - 上游审批的结论键（review/reviewOpinion/reviewBy）：那是上一道审批的审计
          结果，本轮审批人可以把它透传给下游，但不能改写 —— 否则等于替别人补签结论。
        """
        graph = getattr(ctx, "graph", None)
        if graph is None:
            return []
        items = []
        approver_var = approver_field_name(graph)
        for name, entry in self._resolve_pass_through(ctx).items():
            node_id = entry["nodeId"]
            node = graph.get_node(node_id)
            if (node is not None and node.type == "APPROVAL"
                    and entry["varName"] in CONCLUSION_KEYS):
                continue
            item = {"nodeId": node_id, "nodeName": node.label if node else node_id,
                    "varName": entry["varName"], "path": entry["path"],
                    "name": name, "value": entry["value"]}
            # 开始节点的「审批入参」是个例外：它的值契约永远是数组，所以没传/没默认值时
            # 下发 [] 而不是 null —— 否则审批方按 null 渲染成文本框，提交回去就成了
            # 字符串。fieldType 同时告诉审批方这一行要换成审批人（多值）控件。
            if (approver_var and entry["varName"] == approver_var and not entry["path"]
                    and node is not None and node.type == "START"):
                item["fieldType"] = APPROVER_FIELD_TYPE
                item["value"] = clean_approver_list(entry["value"])
            items.append(item)
        return items

    def _approval_context(self, ctx: ExecutionContext) -> dict:
        """本次挂起留下的事实：谁在等、可以改哪几行数据。

        它交给 PauseState 登记（生成 approvalToken、记下回写坐标），不直接给调用方看：
        对外那份待办形状由 approval_projection 从登记结果投出来。
        清单走 `_editable_items`（即本次会透传出去的那批数据）：审批人要改的就是喂给
        下游的那份，两边不同源就会出现「看得到却改不到 / 改了的没放出去」。

        审批人与暂停范围不在这里：前者按图上的审批节点与本轮 inputs 校验（verify_approver），
        后者随 AwaitingApproval.scope 一起交给引擎。
        """
        return {
            "nodeId": self.node.id,
            "nodeName": self.node.label,
            "editableInputs": self._editable_items(ctx),
        }

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        scope = str(data.get("pauseScope") or APPROVAL_SCOPE_DOWNSTREAM).upper()
        if scope not in (APPROVAL_SCOPE_ALL, APPROVAL_SCOPE_DOWNSTREAM):
            issues.append(issue("APPROVAL_SCOPE", "ERROR",
                                f"审批节点暂停范围取值非法: {scope}", node,
                                suggestion="仅支持 ALL（整条工作流）/ "
                                           "DOWNSTREAM（本节点及下游）"))
        if not graph.get_in_edges(node.id):
            issues.append(issue("APPROVAL_NO_INPUT", "ERROR",
                                "审批节点没有上游输入，无数据可审", node))
        if not graph.get_out_edges(node.id):
            issues.append(issue("APPROVAL_NO_OUTPUT", "SUGGESTION",
                                "审批节点没有下游连线，同意后无节点继续执行", node))
        # 子图内暂停不在一期范围：LOOP/ITERATION 循环体、PARALLEL 分支里每轮都会
        # 重新执行审批节点，恢复语义（skip/覆写）在这里不成立
        if node.id in graph.compound_body_node_ids():
            issues.append(issue("APPROVAL_IN_SUBGRAPH", "ERROR",
                                "审批节点不能放在循环/迭代/并行的子图内", node,
                                suggestion="请把审批节点移到主干上"))
        # 透传参数只能校验到「源节点还在 + 输出键名能当引用用」这一层（变量名与子路径
        # 都是节点配置推出来的，两边易漂）：源节点被删时运行期会默默少一个键；
        # 而键名带点/方括号会被当成子路径切分，下游照着引用名永远取不到值。
        unknown: list = []
        bad_names: list = []
        for nid, _var, _path, name in pass_through_selection(data):
            if graph.get_node(nid) is None:
                unknown.append(nid)
            if not PASS_THROUGH_NAME_RE.match(name):
                bad_names.append(name)
        if unknown:
            issues.append(issue("APPROVAL_PASSTHROUGH_SOURCE", "ERROR",
                                f"审批节点「{node.label}」的输出参数引用了不存在的节点: "
                                f"{', '.join(sorted(set(unknown)))}", node,
                                suggestion="上游节点已删除/改号，请重新选择输出参数"))
        if bad_names:
            issues.append(issue("APPROVAL_PASSTHROUGH_NAME", "ERROR",
                                f"审批节点「{node.label}」的输出参数名不合法: "
                                f"{', '.join(sorted(set(bad_names)))}", node,
                                suggestion="下游要用 {{nodes.节点id.名字}} 引用它，名字只能含"
                                           "字母/数字/下划线/中文/连字符，不能含点号与方括号"))
        # 审批人未配置只降级为建议（留空 = 任何持 key 且知道 executionId 者皆可审）
        if not normalize_approvers(data.get("approvers")):
            issues.append(issue("APPROVAL_NO_APPROVER", "SUGGESTION",
                                f"审批节点「{node.label}」未配置审批人", node,
                                suggestion="留空表示任何调用方都能审批，建议显式登记"))
        # 「审批节点 ↔ 开始节点审批入参」的联动校验集中在 graph.validate（逐节点校验会重复报错）
        return issues
