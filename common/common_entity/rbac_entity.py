from datetime import datetime

from sqlalchemy import String, DateTime, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class Role(Base):
    """角色表：data_scope 与 dept_ids 支撑企业级数据权限"""
    __tablename__ = "tb_role"

    role_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    # 数据权限范围：1-全部数据 2-本部门及以下 3-本部门数据 4-仅本人数据 5-自定义部门数据
    data_scope: Mapped[int] = mapped_column(nullable=False, default=4, comment="数据权限范围 1全部 2本部门及以下 3本部门 4仅本人 5自定义")
    # data_scope=5 时的自定义部门范围，逗号分隔的 dept_id 列表
    dept_ids: Mapped[str] = mapped_column(String(1024), nullable=True, comment="自定义数据权限部门ID列表，逗号分隔")
    # 内置角色（ADMIN/USER）受保护，不允许删除
    is_builtin: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Menu(Base):
    """菜单/目录/按钮权限表：menu_type=3 且 perm 非空即按钮权限点"""
    __tablename__ = "tb_menu"

    menu_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(nullable=False, default=0)
    menu_name: Mapped[str] = mapped_column(String(64), nullable=False)
    menu_type: Mapped[int] = mapped_column(nullable=False, default=2)  # 1目录 2菜单 3按钮
    path: Mapped[str] = mapped_column(String(255), nullable=True)
    component: Mapped[str] = mapped_column(String(255), nullable=True)
    perm: Mapped[str] = mapped_column(String(128), nullable=True)
    icon: Mapped[str] = mapped_column(String(64), nullable=True)
    sort: Mapped[int] = mapped_column(nullable=False, default=0)
    # 0-隐藏（不出现在侧边栏，仅权限控制） 1-显示
    visible: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class UserRole(Base):
    """用户-角色关联表"""
    __tablename__ = "tb_user_role"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(nullable=False, index=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class RoleMenu(Base):
    """角色-菜单/权限关联表"""
    __tablename__ = "tb_role_menu"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(nullable=False, index=True)
    menu_id: Mapped[int] = mapped_column(nullable=False, index=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Dept(Base):
    """部门表：数据权限控制的基础组织单元"""
    __tablename__ = "tb_dept"

    dept_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(nullable=False, default=0, comment="父部门ID，0为根")
    dept_name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class OperateLog(Base):
    """系统操作日志表：记录请求级操作明细，trace_id 关联链路追踪"""
    __tablename__ = "tb_operate_log"

    log_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 链路追踪 ID，一次请求全链路唯一
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(nullable=True, index=True, comment="操作人用户ID，未登录为空")
    username: Mapped[str] = mapped_column(String(128), nullable=True)
    module: Mapped[str] = mapped_column(String(64), nullable=True, comment="业务模块，如 user/role/menu")
    operation: Mapped[str] = mapped_column(String(128), nullable=True, comment="操作描述")
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=True, comment="请求参数 JSON")
    ip: Mapped[str] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[int] = mapped_column(nullable=False, default=200)
    cost_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    error_msg: Mapped[str] = mapped_column(String(1000), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), index=True)