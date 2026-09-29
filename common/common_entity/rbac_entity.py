from datetime import datetime

from sqlalchemy import String, DateTime, Integer, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class Role(Base):
    """角色表：data_scope 支撑企业级数据权限"""
    __tablename__ = "tb_role"

    role_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    # 数据权限范围：2-本部门及以下 3-本部门 4-仅本人（1-全部数据 已废弃，全系统范围只由 ADMIN 角色决定）
    data_scope: Mapped[int] = mapped_column(nullable=False, default=4, comment="数据权限范围 2本部门及以下 3本部门 4仅本人")
    # 内置角色（ADMIN/USER）受保护，不允许删除
    is_builtin: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    # 创建人 user_id：数据权限“仅看本人创建的角色”归属依据，存量回填为 0
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True, comment="创建人用户ID")
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
    """部门表：数据权限控制的基础组织单元；一棵树承载集团/子公司/部门三层，不再另立组织表"""
    __tablename__ = "tb_dept"

    dept_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(nullable=False, default=0, comment="父部门ID，0为根")
    dept_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 节点性质：2 为法人组织（集团总部/子公司/板块），1 为普通部门。组织就是树上打了标记的节点，
    # 资源归属只记 owner_dept，组织口径由 org_root_id 派生，避免第二棵树
    node_type: Mapped[int] = mapped_column(nullable=False, default=1, comment="1-普通部门 2-法人组织")
    # 所属组织根（自己或最近的 node_type=2 祖先）的 dept_id，建/改部门时就地重算整棵子孙，检索侧不爬树
    org_root_id: Mapped[int] = mapped_column(nullable=False, default=0, index=True, comment="所属组织根dept_id")
    sort: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class UserGroup(Base):
    """用户组表：跨部门/跨组织的临时人员分组，作为资源 ACL 的一类授权主体"""
    __tablename__ = "tb_user_group"

    group_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class UserGroupMember(Base):
    """用户组成员表：一行一个成员（一人可属多个组）"""
    __tablename__ = "tb_user_group_member"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(nullable=False, index=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ResourceAcl(Base):
    """资源实例 ACL 表：一行 = 一个授权主体对一个资源实例的一个动作。

    只登记显式授权事实；「我创建的」「部门内的」是隐式规则，由归属列现算，不落库，
    否则部门一变动就要刷全量授权行。只做加法（无 deny 行），收紧靠不授权。
    """
    __tablename__ = "tb_resource_acl"

    acl_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    resource_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="资源类型编码")
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="资源实例主键")
    # 授权主体四选一 + 全员；主体类型与主体 id 一起构成唯一键，避免互斥列填 0 的写法
    grantee_type: Mapped[int] = mapped_column(nullable=False, comment="1-用户 2-角色 3-部门 4-用户组 5-全员")
    grantee_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="主体ID，全员时为0")
    # 含下级部门只对 grantee_type=3 生效：1 时该授权覆盖该部门的全部子孙
    dept_include_sub: Mapped[int] = mapped_column(nullable=False, default=0, comment="仅部门授权：0仅本部门 1含下级")
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="单个动作，合法值按资源类型定（见 resource_guard.RESOURCE_SPECS）；长度不得超过 16")
    expire_time: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="过期时间，NULL=永久")
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="授权人，需持有该资源的 share")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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