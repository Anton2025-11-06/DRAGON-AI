from datetime import datetime
from sqlalchemy import String, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from common.common_entity.base_entity import Base


class User(Base):
    """用户表实体，供 login/system/gateway 等服务共享，避免多服务重复定义同一张表"""
    __tablename__ = "tb_user"

    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password: Mapped[str] = mapped_column(String(1000), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=True)
    real_name: Mapped[str] = mapped_column(String(64), nullable=True)
    # 所属部门ID，数据权限控制的基础（0 表示未分配部门）
    dept_id: Mapped[int] = mapped_column(nullable=False, default=0, index=True)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0, comment="")
    # 创建人 user_id：数据权限“仅看本人创建”的归属依据，存量回填为 0
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
