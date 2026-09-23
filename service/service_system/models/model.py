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
    # 能力类型（12 类 code）：见 common.common_constants.model_constant 的 MT_*（text_to_text/... /text_to_audio）
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 供应商：openai(OpenAI兼容)/dashscope(通义千问)/zhipu(智谱)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 模型标识（API 调用时使用）
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型标识")
    base_url: Mapped[str] = mapped_column(String(500), nullable=True, comment="模型接口基础地址(各厂商 OpenAI 兼容/原生基础 URL，端点由 common_model 各类型子类自拼)")
    gateway_url: Mapped[str] = mapped_column(String(500), nullable=True, comment="模型网关地址(展示给调用方)")
    api_key: Mapped[str] = mapped_column(String(500), nullable=True, comment="管理端密钥")
    rate_limit_qps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="每秒并发限制 0=不限")
    # 模型调用参数（temperature/top_k/extra_body 等），JSON 字典；由 common_params 派生，供 common_model 注入
    model_params: Mapped[dict] = mapped_column(JSON, nullable=True, comment="模型调用参数(JSON 字典)")
    # 是否支持流式消息 / 思考模式 / 工具调用（能力标记，决定测试 UI 开关、广场展示
    # 与工作流节点能不能插入工具：未登记的工具调用位会让引擎忽略节点上的 tools）
    supports_stream: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持流消息 1支持 0不支持")
    supports_thinking: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持思考模式 1支持 0不支持")
    supports_function_call: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否支持工具调用 1支持 0不支持")
    # 声明"开启流式/思考"的参数键名（广场展示 + 调用注入）
    stream_param: Mapped[str] = mapped_column(String(64), nullable=True, comment="开启流式的参数键名(默认 stream)")
    thinking_param: Mapped[str] = mapped_column(String(64), nullable=True, comment="开启思考的参数键名")
    # 常用参数富列表 [{name,default,desc,type}]（展示/编辑用；调用注入派生进 model_params）
    common_params: Mapped[list] = mapped_column(JSON, nullable=True, comment="常用参数列表 [{name,default,desc,type}]")
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