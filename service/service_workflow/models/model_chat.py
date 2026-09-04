# -*- coding: utf-8 -*-
"""模型对话 ORM 实体：tb_model_chat_session（会话）/ tb_model_chat_message（消息）"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class ModelChatSession(Base):
    """模型对话-会话表"""
    __tablename__ = "tb_model_chat_session"
    __table_args__ = (Index("idx_user", "user_id", "update_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="所属用户")
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新会话", comment="会话标题")
    # 授权记录 apply_id（模型广场申请通过后生成）
    model_apply_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="授权记录 apply_id")
    model_name: Mapped[str] = mapped_column(String(128), nullable=True, comment="模型标识")
    # 偏好快照：切换会话时恢复
    reasoning: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="深度思考偏好 0关 1开")
    stream: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="流式偏好 0关 1开")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class ModelChatMessage(Base):
    """模型对话-消息表"""
    __tablename__ = "tb_model_chat_message"
    __table_args__ = (Index("idx_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话 id")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色 USER/ASSISTANT")
    content: Mapped[str] = mapped_column(Text, nullable=True, comment="消息内容")
    reasoning_content: Mapped[str] = mapped_column(Text, nullable=True, comment="深度思考内容")
    model_name: Mapped[str] = mapped_column(String(128), nullable=True, comment="模型标识")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())