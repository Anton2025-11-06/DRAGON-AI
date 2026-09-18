# -*- coding: utf-8 -*-
"""智能体资源 ORM 实体（对齐 sql/new_init_.sql 的 tb_mcp_server / tb_tool / tb_skill）。

设计要点：
- status 建表为 TINYINT，实体用 Integer 存取 0/1；对外 bool 转换在 service 层做
  （前端 dict-switch 需要 bool，落库需要 int，口径只在这一处收敛）。
- create_time / update_time 交给 DB 默认值 + onupdate，避免各处手写 NOW() 口径不一。
- id 用 Integer：与 workflow_entity 一致，BIGINT UNSIGNED 主键在 SQLite 变体下无法自增。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class McpServer(Base):
    """MCP 服务器连接配置表（SSE / STDIO 两种模式）"""
    __tablename__ = "tb_mcp_server"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="服务名称")
    # SSE / STDIO
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="SSE", comment="连接类型")
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="SSE 连接地址")
    command: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="STDIO 启动命令")
    args: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True, comment="STDIO 启动参数 JSON 数组")
    env: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True, comment="环境变量 JSON 对象")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="MCP 描述")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class Tool(Base):
    """动态 Python 函数工具表（源码入库，执行走受限沙箱）"""
    __tablename__ = "tb_tool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工具名称")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="工具描述")
    function_code: Mapped[str] = mapped_column(Text, nullable=False, comment="Python 函数源码")
    parameters_schema: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="参数定义 JSON")
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=10000, comment="执行超时（毫秒）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class Skill(Base):
    """技能目录表（zip 原件存公共存储，code 唯一；预览/编辑按需取回解压）"""
    __tablename__ = "tb_skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="技能名称")
    code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, comment="技能标识（唯一）")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="技能描述")
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="分类")
    icon: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="图标")
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="标签 JSON 数组")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    skill_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="展示用逻辑目录路径")
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="资源文件数")
    zip_file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="zip 包原始文件名")
    zip_storage_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="zip 原件在公共存储中的句柄")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
