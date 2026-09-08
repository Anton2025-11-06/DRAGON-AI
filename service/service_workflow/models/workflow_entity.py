# -*- coding: utf-8 -*-
"""工作流编排 ORM 实体（对齐 sql/workflow_schema.sql 的 7 张表）。

设计要点：
- 引擎运行时只读 tb_workflow_version.graph_snapshot（发布快照），
  编辑态写 tb_workflow.graph —— 避免运行中执行读到半编辑状态。
- 执行实例 id 直接用 UUID 字符串（对外即 executionId），免去自增 ID 转 str 的精度问题。
- node_states 聚合 JSON 与 tb_workflow_node_execution 明细双写：
  聚合供列表页快速展示，明细供调试面板逐节点回放。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class Workflow(Base):
    """工作流主表（草稿区）"""
    __tablename__ = "tb_workflow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工作流名称")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # DRAFT / PUBLISHED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", index=True)
    graph: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="图定义（草稿）")
    input_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    output_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class WorkflowVersion(Base):
    """工作流版本快照表"""
    __tablename__ = "tb_workflow_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="图快照")
    input_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    output_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    change_log: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    published: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkflowExecution(Base):
    """工作流执行实例表"""
    __tablename__ = "tb_workflow_execution"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="执行ID(UUID)")
    workflow_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # PENDING/RUNNING/COMPLETED/FAILED/PAUSED/CANCELLED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    # DEBUG/API/AGENT
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="DEBUG")
    inputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    outputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    variables: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="全局变量（断点恢复用）")
    node_states: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="节点状态聚合")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    current_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    breakpoints: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkflowNodeExecution(Base):
    """节点执行明细表（调试面板数据源）"""
    __tablename__ = "tb_workflow_node_execution"

    # sqlite 变体用于集成测试（BIGINT 主键在 SQLite 无法自增），MySQL 生产不变
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # RUNNING/COMPLETED/FAILED/SKIPPED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    node_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WorkflowTemplate(Base):
    """工作流模板表"""
    __tablename__ = "tb_workflow_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # CONVERSATION/GENERATION/RAG/EXTRACTION/SUMMARY/CUSTOM
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="CUSTOM", index=True)
    icon: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    graph: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    output_variables: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_built_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class WorkflowApiKey(Base):
    """工作流 API Key 表"""
    __tablename__ = "tb_workflow_api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    rate_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="每分钟上限 0不限")
    # ACTIVE/REVOKED/EXPIRED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expire_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WorkflowFile(Base):
    """工作流临时文件表"""
    __tablename__ = "tb_workflow_file"

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
