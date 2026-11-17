# -*- coding: utf-8 -*-
"""登录载荷（login_user dict）的唯一装配处。

原先这套组装只长在 `LoginService._build_payload` 里：载荷里的 roles/permissions/
scope_dept_ids/role_ids/group_ids/dept_ancestor_ids 就是 ACL 求值的全部输入
（见 resource_guard.build_visible_cond），所以**任何脱离 HTTP 请求上下文做鉴权的地方**
——arq worker 里的知识检索节点、后台任务、将来的定时触发——都必须拿到同一份载荷。
写在 service_login 里，worker 侧就得反向依赖登录服务；抄一份出来，两处规则早晚分叉
就是越权漏洞的起点（resource_guard 的模块头就是这么说的）。于是把它下沉到 common。

- build_login_payload(session, user)：登录签发时用（调用方自己管事务）；
- load_login_user(user_id)：无请求上下文时用，按 id 现查用户再装配同一份载荷。

注意与 request 链路的差别：JWT 载荷是登录那一刻的快照，角色/部门/组成员变了要重新
登录才生效；`load_login_user` 是**现查**，所以 worker 里拿到的是当前权限，比令牌更新。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_entity.rbac_entity import (
    Dept, Menu, Role, RoleMenu, UserGroup, UserGroupMember, UserRole)
from common.common_entity.user_entity import User
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import (
    DATA_SCOPE_DEPT, DATA_SCOPE_DEPT_AND_CHILD, DATA_SCOPE_SELF,
    get_child_dept_ids, get_dept_ancestor_ids)


async def build_login_payload(session: AsyncSession, user: User) -> dict:
    """
    组装登录载荷：用户基础信息 + 角色集合 + 权限标识集合 + 数据权限范围 + ACL 主体集合
    - permissions：所有角色菜单授权中的按钮权限标识（perm 非空）
    - data_scopes：各角色的数据权限范围（数据权限过滤按最大范围放行）
    - role_ids/dept_ancestor_ids/group_ids：资源 ACL 的四类授权主体（用户/角色/部门/用户组）
      中需要联表才能得到的三个，登录时一次算完，求值侧零查询（与 scope_dept_ids 同口径：
      角色/部门/组成员变化下次重新登录即生效）
    """
    rows = await session.execute(
        select(Menu.perm, Role.role_code, Role.data_scope)
        .join(RoleMenu, RoleMenu.menu_id == Menu.menu_id)
        .join(Role, Role.role_id == RoleMenu.role_id)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user.user_id,
               Role.status == 1, Role.is_deleted == 0,
               Menu.status == 1, Menu.is_deleted == 0,
               Menu.perm.isnot(None), Menu.perm != "")
    )
    perms, roles, data_scopes = set(), set(), set()
    for perm, role_code, data_scope in rows.all():
        if perm:
            perms.add(perm)
        if role_code:
            roles.add(role_code)
        if data_scope:
            data_scopes.add(data_scope)
    # 部门名称写入登录态，供“在线用户”列表展示与关键词检索（旧登录态由查询端按部门表回填）
    dept_name = ""
    if user.dept_id:
        dept_row = await session.execute(
            select(Dept.dept_name).where(Dept.dept_id == user.dept_id, Dept.is_deleted == 0))
        dept_name = dept_row.scalar_one_or_none() or ""
    # 登录时一次算好数据权限部门集合 scope_dept_ids，缓存进载荷供各列表过滤（查询端不再递归）
    scope_dept_ids = await resolve_scope_dept_ids(session, user, "ADMIN" in roles)
    # 角色 id 集合不能从上面的菜单查询里搭：那个 join 只带出「有菜单授权的角色」
    role_ids = sorted({r[0] for r in (await session.execute(
        select(Role.role_id)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user.user_id,
               Role.is_deleted == 0, Role.status == 1))).all()})
    group_ids = sorted({r[0] for r in (await session.execute(
        select(UserGroupMember.group_id)
        .join(UserGroup, UserGroup.group_id == UserGroupMember.group_id)
        .where(UserGroupMember.user_id == user.user_id,
               UserGroup.is_deleted == 0, UserGroup.status == 1))).all()})
    return {
        "user_id": user.user_id, "username": user.username,
        "real_name": user.real_name, "dept_id": user.dept_id or 0,
        "dept_name": dept_name,
        "roles": sorted(roles), "permissions": sorted(perms),
        "data_scopes": sorted(data_scopes),
        "scope_dept_ids": scope_dept_ids,
        "role_ids": role_ids,
        "group_ids": group_ids,
        "dept_ancestor_ids": await get_dept_ancestor_ids(session, user.dept_id or 0),
    }


async def resolve_scope_dept_ids(session: AsyncSession, user: User, is_admin_role: bool):
    """按用户各角色 data_scope 解析可见部门集合（含子部门），多角色取并集：
    - 管理员 → None（不限；「全部数据」档已废弃，非 ADMIN 不可能不限范围）
    - 本部门及以下(2)/本部门(3) → 本人部门（及以下子部门）
    - 仅本人(4) → 不贡献部门（集合为空时即“仅本人”）
    """
    if is_admin_role:
        return None
    rows = await session.execute(
        select(Role.data_scope)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .where(UserRole.user_id == user.user_id, Role.is_deleted == 0, Role.status == 1))
    own_dept = user.dept_id or 0
    dept_ids = set()
    for data_scope in rows.scalars().all():
        scope = data_scope or DATA_SCOPE_SELF
        if scope == DATA_SCOPE_DEPT_AND_CHILD and own_dept:
            dept_ids.add(own_dept)
            dept_ids.update(await get_child_dept_ids(session, own_dept))
        elif scope == DATA_SCOPE_DEPT and own_dept:
            dept_ids.add(own_dept)
    return sorted(dept_ids)


async def load_login_user(user_id: Optional[int]) -> dict:
    """按 user_id 现查一份登录载荷（供无 HTTP 请求上下文的后台执行体做资源鉴权）。

    必须装配完整载荷而不是只塞 `{user_id}`：`get_login_scope` 拿不到 `scope_dept_ids`
    会保守降级成「仅本人」，`is_admin` 拿不到 `roles` 会把管理员当普通用户——
    调用方看不出区别，只会得到一个「明明有权限却查不到数据」的谜。

    :raises ValueError: 用户不存在/已停用/已删除（后台任务的身份失效要说清楚，
        不能拿一份空载荷去鉴权然后返回空结果集）
    """
    uid = int(user_id or 0)
    if uid <= 0:
        raise ValueError("缺少可用的用户身份，无法鉴权")
    async with mysql_client.get_session() as session:
        user = (await session.execute(
            select(User).where(User.user_id == uid))).scalar_one_or_none()
        if user is None or user.is_deleted or not user.status:
            raise ValueError(f"用户 {uid} 不存在或已停用，无法鉴权")
        return await build_login_payload(session, user)
