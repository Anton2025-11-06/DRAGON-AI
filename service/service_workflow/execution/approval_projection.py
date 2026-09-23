# -*- coding: utf-8 -*-
"""PauseState → 消费方读的那份「此刻欠谁」：对外 pendingApprovals 与父侧镜像用的挂起上下文。

读：详情响应、父执行镜像子流程那道审批。本模块不写任何状态。
"""
from __future__ import annotations

from typing import Optional

from service.service_workflow.execution.pause_state import (
    DEFAULT_VALUE_TYPE, REASON_CHILD_APPROVAL, EditableField, PauseState,
    PendingApproval,
)
from service.service_workflow.workflow_engine.nodes.subworkflow_nodes import (
    CHILD_AWAITING_KIND,
)


def public_approvals(pause: PauseState) -> list[dict]:
    """还欠人答的待办 → 对外 pendingApprovals；已答与已转发的都不出现在这里。"""
    return [_public(item) for item in pause.open_approvals()]


def approval_contexts(pause: PauseState,
                      node_states: Optional[dict] = None) -> dict:
    """节点 id → 引擎那份挂起上下文的形状（父侧镜像子审批用），只含还欠人答的节点。

    :param node_states: 取「父等子」时的 childExecutionId（它是节点状态里的事实）
    """
    states = node_states or {}
    contexts = {}
    for item in pause.open_approvals():
        context = {"nodeId": item.node_id, "nodeName": item.node_label,
                   "editableInputs": [_engine_field(field) for field in item.fields]}
        if item.reason == REASON_CHILD_APPROVAL:
            state = states.get(item.node_id)
            context.update({
                "kind": CHILD_AWAITING_KIND,
                "workflowName": item.child_workflow_name,
                "childExecutionId": (state or {}).get("childExecutionId") or "",
                "approvalExecutionId": item.target_execution_id or "",
                "approvalNodeId": item.target_node_id or item.node_id,
                "approvalNodeName": item.approval_node_label,
            })
        contexts[item.node_id] = context
    return contexts


def title_of(item: PendingApproval) -> str:
    """这条待办给人看的那句话；等子流程时说清是哪条子流的哪一道。"""
    if item.reason != REASON_CHILD_APPROVAL:
        return item.node_label
    stage = f"（审批环节：{item.approval_node_label}）" if item.approval_node_label else ""
    return f"子工作流「{item.child_workflow_name or item.node_label}」需要你审批{stage}"


def _public(item: PendingApproval) -> dict:
    """一份待办的对外形状：内部落点坐标与回写三元组都不在这里。"""
    return {"approvalToken": item.approval_token, "nodeId": item.node_id,
            "nodeLabel": item.node_label, "title": title_of(item),
            "approvalNodeLabel": item.approval_node_label or item.node_label,
            "allowedActions": list(item.allowed_actions),
            "editableFields": [field.to_public() for field in item.fields]}


def _engine_field(field: EditableField) -> dict:
    """可编辑字段 → 引擎下发的四元组形状（回写坐标 + 展示名）。"""
    data = {"nodeId": field.source_node_id, "varName": field.source_var_name,
            "path": field.source_path, "name": field.name,
            "nodeName": field.source_node_label, "value": field.value}
    if field.value_type != DEFAULT_VALUE_TYPE:
        data["fieldType"] = field.value_type
    return data
