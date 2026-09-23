# -*- coding: utf-8 -*-
"""「这条执行此刻在等谁」这份事实的唯一载体。

读：详情投影（approval_projection）、订阅端过期判定、提交校验（submit_resolver）。
写：worker 挂起时 register_from_engine_context；API 进程答复后 mark_answered / mark_delivered。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Optional

# 引擎标记「这道审批在子图里」的 kind 值（单向读引擎常量，引擎不反向依赖本包）
from service.service_workflow.workflow_engine.nodes.subworkflow_nodes import (
    CHILD_AWAITING_KIND,
)

# variables 列里本事实所在的键
PAUSE_STATE_KEY = "pauseState"

# 待审批成因：本执行的审批节点在等人，或本执行的【工作流】节点等的那条子执行在等人
REASON_APPROVAL = "APPROVAL"
REASON_CHILD_APPROVAL = "CHILD_APPROVAL"

APPROVE_ACTION = "APPROVE"
REJECT_ACTION = "REJECT"
DEFAULT_ACTIONS = (APPROVE_ACTION, REJECT_ACTION)

# 可编辑字段的取值形态：审批人是一组工号，其余按文本渲染
APPROVER_VALUE_TYPE = "APPROVER"
DEFAULT_VALUE_TYPE = "STRING"


@dataclass
class EditableField:
    """审批面板上的一个可编辑字段；对外只给 name/label/value，回写坐标留在服务端。"""

    name: str
    label: str
    value: Any = None
    value_type: str = DEFAULT_VALUE_TYPE
    required: bool = False
    source_node_id: str = ""
    source_var_name: str = ""
    source_path: str = ""
    source_node_label: str = ""

    @classmethod
    def from_engine_item(cls, item: dict) -> "EditableField":
        """把引擎挂起上下文里的一行可编辑数据登记成字段。"""
        name = str(item.get("name") or item.get("varName") or "")
        return cls(
            name=name,
            label=str(item.get("name") or item.get("varName") or ""),
            value=item.get("value"),
            value_type=str(item.get("fieldType") or DEFAULT_VALUE_TYPE),
            source_node_id=str(item.get("nodeId") or ""),
            source_var_name=str(item.get("varName") or ""),
            source_path=str(item.get("path") or ""),
            source_node_label=str(item.get("nodeName") or ""),
        )

    @classmethod
    def from_registration(cls, data: dict) -> "EditableField":
        """从落库形状还原（与 to_registration 同键）。"""
        return cls(
            name=str(data.get("name") or ""),
            label=str(data.get("label") or data.get("name") or ""),
            value=data.get("value"),
            value_type=str(data.get("valueType") or DEFAULT_VALUE_TYPE),
            required=bool(data.get("required")),
            source_node_id=str(data.get("sourceNodeId") or ""),
            source_var_name=str(data.get("sourceVarName") or ""),
            source_path=str(data.get("sourcePath") or ""),
            source_node_label=str(data.get("sourceNodeLabel") or ""),
        )

    def to_public(self) -> dict:
        """外发形状：一个内部坐标都不带。"""
        return {"name": self.name, "label": self.label, "value": self.value,
                "valueType": self.value_type, "required": self.required,
                "sourceNodeLabel": self.source_node_label}

    def to_registration(self) -> dict:
        """落库形状：外发字段 + 回写所需的 (源节点, 变量名, 子路径)。"""
        data = self.to_public()
        data.update({"sourceNodeId": self.source_node_id,
                     "sourceVarName": self.source_var_name,
                     "sourcePath": self.source_path})
        return data


@dataclass
class PendingApproval:
    """一份待审批：哪个节点在等、结论该落到哪条执行的哪个节点、能改哪些字段。"""

    approval_token: str
    node_id: str
    node_label: str
    reason: str
    fields: list[EditableField] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=lambda: list(DEFAULT_ACTIONS))
    child_workflow_name: str = ""
    # 真正等人点「同意」的那道审批节点的名字：本节点的审批与它相同，等子流程时是子图里那个
    approval_node_label: str = ""
    # 结论落点：两者为空即落在本执行自己这行上（嵌套时由 execution_tree 填子执行坐标）
    target_execution_id: Optional[str] = None
    target_node_id: Optional[str] = None
    answered: bool = False
    delivered_to: Optional[str] = None

    @property
    def is_open(self) -> bool:
        """还没人答 = 还欠着这道审批。"""
        return not self.answered

    def find_field(self, name: str) -> Optional[EditableField]:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def to_engine_edits(self, field_values: dict) -> list[dict]:
        """按字段名把回传值拼成引擎要的四元组（未登记的字段名由调用方先拦下）。"""
        edits: list[dict] = []
        for name, value in (field_values or {}).items():
            item = self.find_field(name)
            if item is None:
                continue
            edits.append({"nodeId": item.source_node_id, "varName": item.source_var_name,
                          "path": item.source_path, "value": value})
        return edits

    def to_dict(self) -> dict:
        return {
            "approvalToken": self.approval_token,
            "nodeId": self.node_id,
            "nodeLabel": self.node_label,
            "reason": self.reason,
            "childWorkflowName": self.child_workflow_name,
            "approvalNodeLabel": self.approval_node_label,
            "allowedActions": list(self.allowed_actions),
            "targetExecutionId": self.target_execution_id,
            "targetNodeId": self.target_node_id,
            "answered": self.answered,
            "deliveredTo": self.delivered_to,
            "fields": [f.to_registration() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PendingApproval":
        return cls(
            approval_token=str(data.get("approvalToken") or ""),
            node_id=str(data.get("nodeId") or ""),
            node_label=str(data.get("nodeLabel") or ""),
            reason=str(data.get("reason") or REASON_APPROVAL),
            fields=[EditableField.from_registration(f)
                    for f in (data.get("fields") or []) if isinstance(f, dict)],
            allowed_actions=list(data.get("allowedActions") or DEFAULT_ACTIONS),
            child_workflow_name=str(data.get("childWorkflowName") or ""),
            approval_node_label=str(data.get("approvalNodeLabel") or ""),
            target_execution_id=data.get("targetExecutionId") or None,
            target_node_id=data.get("targetNodeId") or None,
            answered=bool(data.get("answered")),
            delivered_to=data.get("deliveredTo") or None,
        )


class PauseState:
    """一条执行的挂起事实：待审批清单 + 从行上的 pause_generation 列带来的代次。

    代次不落进本对象的 JSON：它是执行行的一列，重复存一份就会分叉。
    """

    def __init__(self, pause_generation: int = 1,
                 pending: Optional[list[PendingApproval]] = None) -> None:
        self.pause_generation = int(pause_generation or 1)
        self.pending: list[PendingApproval] = list(pending or [])

    # ---------- 登记 ----------

    def register_from_engine_context(self, context: dict) -> str:
        """登记引擎在节点边界抛出的那份挂起上下文，返回新的 approvalToken。

        同一节点重挂即换号：该节点上一份登记（含已答的）整体丢弃，旧凭据自然失效。
        """
        data = context or {}
        node_id = str(data.get("nodeId") or "")
        is_child = data.get("kind") == CHILD_AWAITING_KIND
        self.pending = [item for item in self.pending if item.node_id != node_id]
        token = self._mint_token()
        self.pending.append(PendingApproval(
            approval_token=token,
            node_id=node_id,
            node_label=str(data.get("nodeName") or node_id),
            reason=REASON_CHILD_APPROVAL if is_child else REASON_APPROVAL,
            child_workflow_name=str(data.get("workflowName") or ""),
            approval_node_label=str(data.get("approvalNodeName") or ""),
            target_execution_id=str(data.get("approvalExecutionId") or "") or None
            if is_child else None,
            target_node_id=str(data.get("approvalNodeId") or "") or node_id,
            fields=[EditableField.from_engine_item(item)
                    for item in (data.get("editableInputs") or []) if isinstance(item, dict)],
        ))
        return token

    def add(self, item: PendingApproval) -> str:
        """直接登记一份已构造好的待审批（跨执行搬运用），返回其凭据。"""
        self.pending = [kept for kept in self.pending if kept.node_id != item.node_id]
        self.pending.append(item)
        return item.approval_token

    # ---------- 取用 ----------

    def find(self, approval_token: str) -> Optional[PendingApproval]:
        """按凭据取还欠着的待审批；答过或已换号的取不到（调用方据此判重复审批）。"""
        for item in self.pending:
            if item.approval_token == approval_token and item.is_open:
                return item
        return None

    def find_any(self, approval_token: str) -> Optional[PendingApproval]:
        """按凭据取待审批，含已答的（只为排障日志与「答过没有」的区分）。"""
        for item in self.pending:
            if item.approval_token == approval_token:
                return item
        return None

    def open_approvals(self) -> list[PendingApproval]:
        """还欠人答的待审批；为空即本轮可以继续推进。"""
        return [item for item in self.pending if item.is_open]

    def is_awaiting_human(self) -> bool:
        return bool(self.open_approvals())

    def open_by_node(self, node_id: str) -> Optional[PendingApproval]:
        """按节点取那份还欠着的待审批（结论落点判定用）。"""
        for item in self.open_approvals():
            if item.node_id == node_id:
                return item
        return None

    # ---------- 变更 ----------

    def mark_answered(self, approval_token: str) -> bool:
        """把这份待审批标成已答（结论已进本轮 RoundRequest）。"""
        item = self.find(approval_token)
        if item is None:
            return False
        item.answered = True
        return True

    def mark_delivered(self, approval_token: str, target_execution_id: str) -> bool:
        """记「结论已交给这条执行去跑」：清单里仍看得到，但不再接受第二次投递。"""
        item = self.find(approval_token)
        if item is None:
            return False
        item.answered = True
        item.delivered_to = target_execution_id
        return True

    def invalidate_all(self) -> int:
        """作废全部未答待审批（重开一轮与取消走这里），返回作废条数。"""
        opened = self.open_approvals()
        for item in opened:
            item.answered = True
        return len(opened)

    # ---------- 编解码 ----------

    def to_dict(self) -> dict:
        return {"pendingApprovals": [item.to_dict() for item in self.pending]}

    @classmethod
    def from_dict(cls, data: Optional[dict],
                 pause_generation: int = 1) -> "PauseState":
        """从落库形状还原；代次由调用方从 pause_generation 列取。"""
        payload = data if isinstance(data, dict) else {}
        return cls(
            pause_generation=int(pause_generation or 1),
            pending=[PendingApproval.from_dict(item)
                     for item in (payload.get("pendingApprovals") or [])
                     if isinstance(item, dict)],
        )

    def _mint_token(self) -> str:
        """生成一个本清单内不重复的短凭据（对外主键，不参与任何解析）。"""
        used = {item.approval_token for item in self.pending}
        token = secrets.token_hex(4)
        while token in used:
            token = secrets.token_hex(4)
        return token
