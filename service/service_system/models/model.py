# -*- coding: utf-8 -*-
"""模型广场 ORM 实体：tb_model（模型）/ tb_model_apply（申请审批）"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class Model(Base):
    """模型广场-模型表"""
    __tablename__ = "tb_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名称")
    # 分类枚举见 common.common_constants.model_constant 的 MODEL_CATEGORY_*（7 类）
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 提供商：deepseek/qwen/doubao/hunyuan/kimi/openai
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 模型标识（API 调用时使用）
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型标识")
    base_url: Mapped[str] = mapped_column(String(500), nullable=True, comment="模型真实地址(直连=含接口完整路径，非直连=接口基础地址)")
    gateway_url: Mapped[str] = mapped_column(String(500), nullable=True, comment="模型网关地址(展示给调用方)")
    # 是否直连：1=直连(base_url含完整接口路径) 0=非直连(base_url+接口后缀转发)
    is_direct: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="是否直连 1直连 0非直连")
    # 非直连时支持的后缀列表：[{"url": "/v1/chat/completions", "desc": "对话接口"}]
    suffixes: Mapped[list] = mapped_column(JSON, nullable=True, comment="接口后缀列表(JSON 数组)")
    api_key: Mapped[str] = mapped_column(String(500), nullable=True, comment="管理端密钥")
    rate_limit_qps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="每秒并发限制 0=不限")
    # 模型调用参数（temperature/top_k/extra_body 等），JSON 字典
    model_params: Mapped[dict] = mapped_column(JSON, nullable=True, comment="模型调用参数(JSON 字典)")
    tutorial_md: Mapped[str] = mapped_column(Text, nullable=True, comment="使用教程 Markdown")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True, comment="启用状态 1启用 0停用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人 user_id")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class ModelApply(Base):
    """模型广场-申请审批表"""
    __tablename__ = "tb_model_apply"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=True)
    dept_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(String(500), nullable=True, comment="申请理由")
    # 0 待审批 1 已通过 2 已拒绝
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    # 审批通过后签发的 API Key
    api_key: Mapped[str] = mapped_column(String(500), nullable=True)
    reject_reason: Mapped[str] = mapped_column(String(500), nullable=True, comment="拒绝原因")
    audit_by: Mapped[str] = mapped_column(String(64), nullable=True, comment="审批人")
    audit_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    apply_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())