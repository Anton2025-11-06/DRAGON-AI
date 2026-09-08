# -*- coding: utf-8 -*-
"""工作流 API Schema（请求/响应模型，字段与前端 types.ts 一一对应）。"""
from __future__ import annotations

from typing import Any, Optional

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


class WorkflowExecutionReq(BaseModel):
    inputs: Optional[dict] = None
    breakpoints: Optional[list[str]] = Field(None, description="断点节点ID列表")
    breakpointConditions: Optional[dict[str, str]] = Field(
        None, description="条件断点：nodeId → 表达式（{{ref}} 变量引用 + 比较/逻辑运算，true 才暂停）")


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


# ==================== 内部 DTO（service → router） ====================

class ExecutionStartedDto(BaseModel):
    """execute-async 立即返回体（前端契约为 string executionId，服务层直接返回 str）"""
    execution_id: str
