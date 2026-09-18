# -*- coding: utf-8 -*-
"""APPROVAL 人工审批节点（需求 2）：在节点边界挂起，等「指定 executionId 再提交」接口带回结论。

设计口径（docs/workflow-approval-memory.md 第 5 节）：
- 单出口：同意 → 正常路由下游；不同意 → 下游可达闭包整体取消，其余分支照常跑完；
- 输出：透传上游数据 + `review`(bool) + `reviewOpinion` + `reviewBy`；
- 审批人识别：画布有审批节点时，START 节点才允许一个 `type=APPROVER` 的入参字段，
  提交时该入参（数组）与审批节点 `approvers` 求交集判权限；
- 编辑回写：同意时对 `(源节点id, 变量名, 当前值)` 三元组里的值做修改，回写源节点输出。

本模块同时承载「审批人字段」的读取口径：提交接口（service 层）与节点执行器共用，
避免两处各写一份 START 字段解析。
"""
from __future__ import annotations

from typing import Any, Optional

from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.nodes.base import (
    APPROVAL_SCOPE_ALL, APPROVAL_SCOPE_DOWNSTREAM, AwaitingApproval,
    BaseNodeExecutor, NodeResult, issue,
)

# START 输入字段的「审批入参」类型标记（与前端 types.ts InputField.type 一致）
APPROVER_FIELD_TYPE = "APPROVER"


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


def approval_nodes(graph) -> list:
    """图内全部审批节点（按画布顺序）。"""
    return [n for n in graph.nodes if n.type == "APPROVAL"] if graph is not None else []


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
        # 本轮结论：取出即消费（同一提交重复执行到该节点时不应再命中）
        decision = self.runtime.approval_decisions.pop(node_id, None)
        if decision is None:
            # 没有结论 → 请求引擎在节点边界落暂停。不在此 await 等审批：
            # 那会占住 worker 任务槽并受节点超时约束，一晚上不点同意就把节点判失败。
            raise AwaitingApproval(node_id, self._approval_context(ctx), self._scope(cfg))

        approved = bool(decision.get("approved"))
        opinion = str(decision.get("opinion") or "")
        review_by = str(decision.get("reviewBy") or "") or None
        diff: list = []
        if approved:
            # 同意：先回写编辑（改源节点 output + ctx），再取透传 → 拿到的是编辑后的值
            diff = await self.runtime.apply_output_edits(node_id, decision.get("edits") or [])
        else:
            await self.runtime.cancel_downstream(
                node_id, f"审批未通过：{opinion or '未填写审批意见'}",
                reject_reply=str(cfg.get("rejectReply") or ""))

        output = {**self.input_pass_through(ctx), "review": approved,
                  "reviewOpinion": opinion, "reviewBy": review_by or ""}
        state = self.runtime.node_states.get(node_id)
        if state is not None:
            # 审批审计随 node_states 落库（跨轮恢复与执行详情都读这里）
            state.review = approved
            state.reviewBy = review_by
            state.reviewOpinion = opinion
            state.reviewDiff = diff
        return NodeResult(output=output)

    # ---------- 配置与上下文 ----------

    @staticmethod
    def _scope(cfg: dict) -> str:
        return (APPROVAL_SCOPE_ALL
                if str(cfg.get("pauseScope") or "").upper() == APPROVAL_SCOPE_ALL
                else APPROVAL_SCOPE_DOWNSTREAM)

    def _approval_context(self, ctx: ExecutionContext) -> dict:
        """外发给审批方的上下文：可编辑的三元组清单 + 审批人配置。

        只列**直接入边来源**节点输出的顶层键：审批人要改的就是喂给下游的那批数据，
        递归铺全图既看不到边界，也会把上轮大输出（检索文档数组等）整个搬进事件。
        """
        items = []
        for edge in self.graph.get_in_edges(self.node.id) or []:
            src = self.graph.get_node(edge.source)
            output = ctx.get_node_output(edge.source)
            if src is None or not isinstance(output, dict):
                continue
            for var, value in output.items():
                items.append({"nodeId": src.id, "nodeName": src.label,
                              "varName": var, "value": value})
        cfg = self.config
        return {
            "nodeId": self.node.id,
            "nodeName": self.node.label,
            "approvers": cfg.get("approvers") or [],
            "pauseScope": self._scope(cfg),
            "rejectReply": cfg.get("rejectReply") or "",
            "timeoutHours": cfg.get("timeoutHours") or 0,
            "editableInputs": items,
        }

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        scope = str(data.get("pauseScope") or APPROVAL_SCOPE_DOWNSTREAM).upper()
        if scope not in (APPROVAL_SCOPE_ALL, APPROVAL_SCOPE_DOWNSTREAM):
            issues.append(issue("APPROVAL_SCOPE", "ERROR",
                                f"审批节点暂停范围取值非法: {scope}", node,
                                suggestion="仅支持 ALL（整条工作流）/ DOWNSTREAM（本节点及下游）"))
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
                                suggestion="请把审批节点移到主干上（暂停只发生在整条流的节点边界）"))
        # 审批人未配置只降级为建议（留空 = 任何持 key 且知道 executionId 者皆可审）
        if not normalize_approvers(data.get("approvers")):
            issues.append(issue("APPROVAL_NO_APPROVER", "SUGGESTION",
                                f"审批节点「{node.label}」未配置审批人", node,
                                suggestion="留空表示任何调用方都能审批，建议显式登记"))
        # 「审批节点 ↔ 开始节点审批入参」的联动校验集中在 graph.validate（逐节点校验会重复报错）
        return issues
