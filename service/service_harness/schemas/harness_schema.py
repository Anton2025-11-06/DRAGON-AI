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


class AttachmentItem(BaseModel):
    """附件引用：文件已由前端上传进沙箱工作区，这里只带文件名与工作区绝对路径。

    不做后端预解析：模型自带 Read/Glob/Grep/Bash 等工具，会在本轮自行读取解析；
    服务端只把这个路径告知模型「工作区里有这个文件」。
    """

    name: str = Field(..., max_length=256, description="文件名")
    path: str = Field(..., max_length=1024, description="沙箱工作区绝对路径")


class ChatReq(BaseModel):
    """发一轮对话：建 turn（USER 行 + ASSISTANT 行 RUNNING）→ 投 arq harness 流水线。

    模型与挂载都由本轮请求携带（同一会话里可随时切换），落到消息行供 worker 取用。
    """

    sessionId: int = Field(..., ge=1, description="会话 id")
    message: str = Field(..., min_length=1, max_length=8000, description="本轮用户输入")
    # 选了智能体（orchConfigId>0）时可缺省：本轮用该编排配置里自带的模型；
    # 传了则覆盖执行角色的自带模型（页面文案「选择该模型，将会覆盖智能体已配置的模型」）
    modelId: Optional[int] = Field(
        None, ge=1, description="本轮选用的模型 id（走编排配置时可不传）")
    mounts: Optional[MountsReq] = Field(
        None, description="本轮挂载快照（不传=本轮不挂载任何能力）")
    attachments: Optional[list[AttachmentItem]] = Field(
        None, max_length=20, description="本轮附件（已上传到沙箱工作区的文件引用，落 USER 行供 worker 注入提示）")
    # 智能体编排：深度探索页「+智能体」选了某个已保存的编排配置（四模式之一），
    # 复用与纯深度探索完全相同的 /chat 管线，只多带这两个字段落到轮次行，
    # worker 据此分派。缺省 single/0 = 深度探索原路径（行为不变）。
    orchMode: Optional[str] = Field(
        None, pattern="^(single|team|goal|sop)$",
        description="编排模式（页面选了智能体时传入；不传=纯深度探索）")
    orchConfigId: Optional[int] = Field(
        None, ge=0, description="编排配置 id（tb_orch_* 主键；不传/0=深度探索）")
