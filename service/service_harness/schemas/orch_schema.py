# -*- coding: utf-8 -*-
"""智能体编排 API Schema（四模式请求体，camelCase 直写，对齐 harness_schema 风格）。

设计口径：
- mounts 直接复用 harness_schema.MountsReq 的四类能力 id 列表，另加 modelId
  （单智能体把「用哪个模型」也当作配置的一部分持久化，验证台/运行时据此取模型）；
- 团队/目标/SOP 的成员既可从单智能体列表克隆（传 source_agent_id），也可页面
  自定义（直接给 name/systemPrompt/mounts）；成员配置改动不回写 tb_orch_agent
  （克隆语义，落各自表的 config_json 快照）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from service.service_harness.schemas.harness_schema import MountsReq


class OrchAgentMounts(MountsReq):
    """单智能体挂载：四类能力 id 列表 + 模型 id（模型随配置持久化）。"""

    modelId: Optional[int] = Field(None, ge=1, description="模型 id（运行时据此取模型）")


class OrchAgentSaveReq(BaseModel):
    """单智能体新增/编辑：名称/用途/系统提示词 + 挂载（技能/工具/MCP/知识库/模型）。"""

    name: str = Field(..., min_length=1, max_length=128, description="智能体名称")
    purpose: Optional[str] = Field(None, max_length=500, description="用途")
    systemPrompt: Optional[str] = Field(None, description="系统提示词")
    mounts: Optional[OrchAgentMounts] = Field(None, description="挂载（技能/工具/MCP/知识库/模型）")
    status: Optional[bool] = Field(True, description="0-停用 1-启用")


# ==================== 团队 / 目标 / SOP 成员规格（克隆 or 自定义） ====================
class OrchMemberSpec(BaseModel):
    """成员/执行者/检查者规格：从单智能体克隆（sourceAgentId）或页面自定义。

    克隆语义（计划 §6）：给了 sourceAgentId 即从 tb_orch_agent 取该行的
    name/systemPrompt/mounts 作为初值（mounts 逐成员按 use 口径过滤），
    再允许用本请求里的字段覆盖（自定义编辑不回写单智能体表）。
    """

    sourceAgentId: Optional[int] = Field(None, ge=1, description="克隆来源单智能体 id")
    name: Optional[str] = Field(None, max_length=128, description="成员名称（自定义/覆盖）")
    description: Optional[str] = Field(None, max_length=500,
                                       description="成员职责（团队供队长分派 / SOP 步骤留空）")
    systemPrompt: Optional[str] = Field(None, description="系统提示词（自定义/覆盖）")
    mounts: Optional[OrchAgentMounts] = Field(None, description="挂载（自定义/覆盖）")


class OrchTeamSaveReq(BaseModel):
    """团队新增/编辑：主智能体（队长）+ 成员列表，均支持克隆或自定义。"""

    name: str = Field(..., min_length=1, max_length=128, description="团队名称")
    purpose: Optional[str] = Field(None, max_length=500, description="用途")
    leader: OrchMemberSpec = Field(..., description="主智能体（队长）")
    members: list[OrchMemberSpec] = Field(..., min_length=1, description="成员列表")
    status: Optional[bool] = Field(True, description="0-停用 1-启用")


class OrchGoalSaveReq(BaseModel):
    """目标新增/编辑：执行者 + 验证者（最多两个智能体，共用一份 state）。"""

    name: str = Field(..., min_length=1, max_length=128, description="目标名称")
    purpose: Optional[str] = Field(None, max_length=500, description="用途")
    executor: OrchMemberSpec = Field(..., description="执行者")
    verifier: OrchMemberSpec = Field(..., description="验证者（检查者）")
    status: Optional[bool] = Field(True, description="0-停用 1-启用")


class OrchSopStepSpec(BaseModel):
    """SOP 流程节点：名称/流程定义/执行者/检查者/最大驳回次数（默认 3）。"""

    subject: str = Field(..., min_length=1, max_length=128, description="节点名称")
    description: Optional[str] = Field(None, max_length=1000, description="流程定义（该节点要达成什么）")
    executor: OrchMemberSpec = Field(..., description="执行者")
    verifier: Optional[OrchMemberSpec] = Field(None, description="检查者（留空则执行完即通过）")
    maxAttempts: int = Field(3, ge=1, le=20, description="最大驳回次数")


class OrchSopSaveReq(BaseModel):
    """SOP 新增/编辑：名称/用途 + 流程节点（数组序即流程顺序）。"""

    name: str = Field(..., min_length=1, max_length=128, description="SOP 名称")
    purpose: Optional[str] = Field(None, max_length=500, description="用途描述")
    steps: list[OrchSopStepSpec] = Field(..., min_length=1, description="流程节点（按序）")
    status: Optional[bool] = Field(True, description="0-停用 1-启用")
