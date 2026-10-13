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
    """新建会话：只建一个空壳（标题），模型与挂载都是每一轮发消息时现选，不在会话上绑。"""

    title: str = Field("新会话", min_length=1, max_length=128, description="会话标题")


class SessionUpdateReq(BaseModel):
    """编辑会话：只能改标题与归档状态（模型/挂载不绑会话，改它们属于发消息时的事）。"""

    title: Optional[str] = Field(None, min_length=1, max_length=128)
    status: Optional[str] = Field(None, pattern="^(ACTIVE|ARCHIVED)$",
                                  description="会话状态 ACTIVE/ARCHIVED")


class ChatReq(BaseModel):
    """发一轮对话：建 turn（USER 行 + ASSISTANT 行 RUNNING）→ 投 arq harness 流水线。

    模型与挂载都由本轮请求携带（同一会话里可随时切换），落到消息行供 worker 取用。
    """

    sessionId: int = Field(..., ge=1, description="会话 id")
    message: str = Field(..., min_length=1, max_length=8000, description="本轮用户输入")
    modelId: int = Field(..., ge=1, description="本轮选用的模型 id")
    mounts: Optional[MountsReq] = Field(
        None, description="本轮挂载快照（不传=本轮不挂载任何能力）")
