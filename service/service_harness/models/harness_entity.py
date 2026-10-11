# -*- coding: utf-8 -*-
"""深度探索 harness 智能体 ORM 实体（对齐 sql/v2_init.sql PART 7）。

设计要点（与 tb_model_chat_* 同款范式）：
- 会话行持挂载快照（mounts JSON）与模型授权（model_apply_id），发消息时以此
  组装 toolkit；执行侧（arq worker）另有 ACL 二次校验，快照不是信任边界。
- 消息按轮（turn）组织：一轮对话 = USER 行 + ASSISTANT 行同 turn_id；turn_id
  同时是 arq job_id 与事件频道后缀，幂等投递、发布订阅、SSE 回放共用这一个键。
- 运行态（AgentState 记忆窗口/HITL 待答上下文）在 Redis（agentscope
  RedisStorage），沙箱文件在 OpenSandbox，都不在 MySQL——这里只有业务事实。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base

# 轮次状态（ASSISTANT 行权威；USER 行只作内容回显，状态恒 COMPLETED）
TURN_RUNNING = "RUNNING"
TURN_COMPLETED = "COMPLETED"
TURN_FAILED = "FAILED"
# HITL 挂起等用户答复（AskUser / 工具授权确认）：下一轮用户输入即恢复
TURN_INTERRUPTED = "INTERRUPTED"

# 会话状态
SESSION_ACTIVE = "ACTIVE"
SESSION_ARCHIVED = "ARCHIVED"


class HarnessSession(Base):
    """深度探索-会话表"""
    __tablename__ = "tb_harness_session"
    __table_args__ = (Index("idx_hs_user", "user_id", "update_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属用户")
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新会话", comment="会话标题")
    # 授权记录 apply_id（模型广场审批通过后签发，模型密钥从 tb_model_apply 取）
    model_apply_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="授权记录 apply_id")
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="模型标识快照")
    # 挂载快照 {kbIds:[], mcpIds:[], toolIds:[], skillIds:[]}（展示用；执行侧另有 ACL 校验）
    mounts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="挂载快照")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SESSION_ACTIVE,
                                        comment="状态 ACTIVE/ARCHIVED")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class HarnessMessage(Base):
    """深度探索-消息表（按轮 turn 组织；一轮两行 USER/ASSISTANT）"""
    __tablename__ = "tb_harness_message"
    __table_args__ = (Index("idx_hm_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话 id")
    # 轮次 UUID：= arq job_id = harness:evt:{turn_id} 频道后缀（一轮两行共用）
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="轮次 UUID")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色 USER/ASSISTANT")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="消息正文")
    thinking: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="思考流全文")
    # 工具调用轨迹 [{tool, arguments, state}]（result 的裁剪口径同工作流 hide_tool_call_results）
    tool_calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="工具调用轨迹")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TURN_RUNNING,
                                        comment="轮次状态 RUNNING/COMPLETED/FAILED/INTERRUPTED")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="失败原因")
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="本轮模型标识")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
