# -*- coding: utf-8 -*-
"""工作流 API Schema（请求/响应模型，字段与前端 types.ts 一一对应）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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


class WorkflowExecutionReq(BaseModel):
    inputs: Optional[dict] = None


class ApprovalEditReq(BaseModel):
    """审批表单里被改过的一行：(源节点 id, 变量名, 新值) 三元组。"""
    nodeId: str = Field(..., description="源节点 id（三元组第一项）")
    varName: str = Field(..., description="源节点输出里的变量名")
    value: Any = Field(None, description="改后的新值（任意 JSON 形态）")


class ApprovalDecisionReq(BaseModel):
    """本轮审批结论（只针对一个审批节点）。"""
    nodeId: Optional[str] = Field(None, description="审批节点 id；仅有一个待审批节点时可省略")
    approved: bool = Field(..., description="true=同意，false=不同意（取消下游）")
    opinion: Optional[str] = Field(None, max_length=2000, description="审批意见")
    edits: Optional[list[ApprovalEditReq]] = Field(None, description="同意时对上游数据的编辑")


class WorkflowSubmitReq(BaseModel):
    """指定 executionId 的再提交入参（需求 3：暂停后恢复 / 重新执行）。"""
    mode: str = Field(..., description="RETRY 重新执行（全量重跑）/ CONTINUE 暂停后恢复（已完成节点跳过）")
    inputs: Optional[dict] = Field(None, description="本轮输入；不传沿用上轮行内 inputs")
    approval: Optional[list[ApprovalDecisionReq]] = Field(
        None, description="审批结论列表（存在待审批节点时必填；并行多个待审批时每节点一条）")

    @field_validator("approval", mode="before")
    @classmethod
    def _wrap_approval(cls, v):
        # 只有一个待审批节点时允许直接传对象，省掉前端数组包装
        return [v] if isinstance(v, dict) else v


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


# ==================== 内部 DTO（service → router） ====================

class ExecutionStartedDto(BaseModel):
    """execute-async 立即返回体（前端契约为 string executionId，服务层直接返回 str）"""
    execution_id: str
