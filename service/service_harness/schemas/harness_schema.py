# -*- coding: utf-8 -*-
"""深度探索 harness API Schema（请求/响应模型，风格对齐 workflow_schema 的 camelCase 直写）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MountsReq(BaseModel):
    """挂载快照（四类能力 id 列表）。

    只是一份「想挂什么」的声明：执行侧（arq worker）组装 toolkit 前还有
    ensure_action 二次校验，快照本身不是信任边界。
    """

    kbIds: list[int] = Field(default_factory=list, description="知识库 id")
    mcpIds: list[int] = Field(default_factory=list, description="MCP 服务器 id")
    toolIds: list[int] = Field(default_factory=list, description="平台工具 id")
    skillIds: list[int] = Field(default_factory=list, description="技能 id")


class SessionCreateReq(BaseModel):
    title: str = Field("新会话", min_length=1, max_length=128, description="会话标题")
    modelApplyId: int = Field(0, ge=0, description="模型授权记录 apply_id（0=暂未选模型）")
    mounts: Optional[MountsReq] = Field(None, description="初始挂载快照")


class SessionUpdateReq(BaseModel):
    """编辑会话：只改传了的字段（用 model_fields_set 区分「没传」与「传 null」）。"""

    title: Optional[str] = Field(None, min_length=1, max_length=128)
    modelApplyId: Optional[int] = Field(None, ge=0)
    mounts: Optional[MountsReq] = None
    status: Optional[str] = Field(None, pattern="^(ACTIVE|ARCHIVED)$",
                                  description="会话状态 ACTIVE/ARCHIVED")


class ChatReq(BaseModel):
    """发一轮对话：建 turn（USER 行 + ASSISTANT 行 RUNNING）→ 投 arq harness 流水线。"""

    sessionId: int = Field(..., ge=1, description="会话 id")
    message: str = Field(..., min_length=1, max_length=8000, description="本轮用户输入")
    mounts: Optional[MountsReq] = Field(
        None, description="不传=沿用会话挂载快照；传=覆盖会话快照并本轮即生效")
