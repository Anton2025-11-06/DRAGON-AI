# -*- coding: utf-8 -*-
"""工作流 API Schema（请求/响应模型，字段与前端 types.ts 一一对应）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ==================== 通用 ====================

class PageReq(BaseModel):
    current: int = Field(1, ge=1, description="当前页码")
    size: int = Field(10, ge=1, le=200, description="每页大小")


# ==================== 工作流定义 ====================

class VariableDefinition(BaseModel):
    name: str
    type: str = "string"  # string/number/boolean/object/array
    defaultValue: Any = None
    description: Optional[str] = None
    required: bool = False


class WorkflowSaveReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="工作流名称")
    description: Optional[str] = Field(None, max_length=500)
    graph: Optional[dict] = None
    inputVariables: Optional[list[VariableDefinition]] = None
    outputVariables: Optional[list[VariableDefinition]] = None
    changeLog: Optional[str] = Field(None, max_length=500, description="变更说明")


# ==================== 执行提交（唯一入口的唯一动作） ====================

class ApprovalDecisionReq(BaseModel):
    """一份审批结论；凭哪份待办答、答什么、改了哪些字段。"""
    approvalToken: str = Field(..., description="pendingApprovals[].approvalToken")
    action: Literal["APPROVE", "REJECT"] = Field(
        ..., description="同意 / 不同意；两者都继续走下游，分支由下游条件节点拿 review 自己判")
    fieldValues: dict = Field(default_factory=dict,
                              description="键 = editableFields[].name，值 = 改后的完整值")
    opinion: Optional[str] = Field(None, max_length=2000, description="审批意见")


class WorkflowSubmitReq(BaseModel):
    """submit 的唯一入参；提交意图全由「给了哪几个键」表达，不出现 mode。"""
    executionId: Optional[str] = Field(
        None, description="不传=新建会话；传=对已有会话的任一后续动作")
    workflowId: Optional[int] = Field(None, description="新建时必填；传了 executionId 时可省")
    values: Optional[dict] = Field(None, description="业务输入；不传沿用上轮 inputs")
    decisions: Optional[list[ApprovalDecisionReq]] = Field(
        None, description="审批结论；有未答审批时的唯一推进方式，一份待办一条")
    restart: bool = Field(False, description="放弃未答审批并全量重跑；仅带 executionId 时有意义")


class SubmitResult(BaseModel):
    """submit 的唯一返回体；四种意图同形，调用方不需按分支解析。"""
    executionId: str
    status: str = Field(..., description="RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED")
    pauseGeneration: int = Field(..., description="本次提交后的当前挂起代次")
    duplicated: bool = Field(False, description="重复答复同一份审批凭据：后台未做任何动作")
    pendingApprovals: list[dict] = Field(default_factory=list, description="提交后仍欠的审批")


class TemplateSaveReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=500)
    category: str = Field("CUSTOM", description="CONVERSATION/GENERATION/RAG/EXTRACTION/SUMMARY/CUSTOM")
    icon: Optional[str] = Field(None, max_length=64)
    graph: Optional[dict] = None
    inputVariables: Optional[list[VariableDefinition]] = None
    outputVariables: Optional[list[VariableDefinition]] = None


class ApiKeyCreateReq(BaseModel):
    workflowId: int
    name: str = Field(..., min_length=1, max_length=128)
    rateLimit: int = Field(0, ge=0, description="每秒调用上限(QPS) 0=不限",)
    expireDays: Optional[int] = Field(None, ge=1, le=3650, description="有效天数")


class ApiKeyUpdateReq(BaseModel):
    """编辑 API Key：只改传了的字段。

    expireTime 区分两种语义：不传=不改；显式传 null=改为永不过期
    （靠 model_fields_set 判定是否传过，所以不能只判 None）。
    """
    name: Optional[str] = Field(None, min_length=1, max_length=128, description="备注名称")
    rateLimit: Optional[int] = Field(None, ge=0, description="每秒调用上限(QPS) 0=不限")
    expireTime: Optional[datetime] = Field(None, description="过期时间，null=永不过期")
