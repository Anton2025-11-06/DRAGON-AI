from functools import wraps
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_constants.constant import PREFIX_LOGIN
from common.common_middleware.exception_handler import UnauthorizedException
from common.common_redis import redis
from common.common_utils.jwt_util import decode_token


async def extract_token(request: Request) -> str:
    """从请求头解析gateway注入的X-User-Token"""
    return request.headers.get("X-User-Token")


async def get_login_user(request: Request) -> dict:
    """
    获取当前登录用户信息（token 载荷）：
    - 若 TokenCheckMiddleware 已解析（request.state.login_user），直接复用，避免重复查 Redis
    - 否则回退：JWT 解析载荷 / Redis login_{token} 查询
    """
    cached = request.scope.get("login_user") or getattr(request.state, "login_user", None)
    if cached:
        return cached

    token = await extract_token(request)
    # 网关链路必注入 X-User-Token；缺失（如直连/旁路）时视为未登录，避免 decode_token(None) 崩溃成 500
    if not token:
        raise UnauthorizedException("未登录，禁止操作！")
    # JWT 载荷自带完整用户信息
    payload = decode_token(token)
    if payload:
        return payload
    # 兼容旧格式 token：载荷存于 Redis
    data = await redis.client.get(PREFIX_LOGIN + token, to_dict=True)
    if not data:
        raise UnauthorizedException("未登录，禁止操作！")
    return data


async def get_token(request: Request) -> str:
    """获取原始 token 字符串"""
    return await extract_token(request)


def is_admin(login_user: dict) -> bool:
    """超级管理员判断：角色集合包含 ADMIN"""
    return "ADMIN" in login_user.get("roles", [])


def has_permission(perm: str):
    """
    菜单权限校验装饰器：要求登录用户权限标识集合包含 perm（ADMIN 角色直接放行）
    用法示例：@has_permission("system:user:add")
    """
    def outer(func):
        @wraps(func)
        async def inner(request: Request, *args, **kwargs):
            login_user = await get_login_user(request)
            if not is_admin(login_user) and perm not in login_user.get("permissions", []):
                raise UnauthorizedException("权限不足，禁止操作！")
            return await func(request, *args, **kwargs)

        return inner

    return outer


def require_permission(*perms: str, require_all: bool = False):
    """
    灵活的多权限校验装饰器：
    :param perms: 权限标识列表
    :param require_all: True-需满足全部权限；False-满足任一即可（默认）
    """
    def outer(func):
        @wraps(func)
        async def inner(request: Request, *args, **kwargs):
            login_user = await get_login_user(request)
            if is_admin(login_user):
                return await func(request, *args, **kwargs)
            user_perms = login_user.get("permissions", [])
            matched = [p for p in perms if p in user_perms]
            ok = len(matched) == len(perms) if require_all else len(matched) > 0
            if not ok:
                raise UnauthorizedException("权限不足，禁止操作！")
            return await func(request, *args, **kwargs)

        return inner

    return outer


# ==================== 数据权限（Data Scope）====================
DATA_SCOPE_ALL = 1            # 全部数据
DATA_SCOPE_DEPT_AND_CHILD = 2  # 本部门及以下
DATA_SCOPE_DEPT = 3           # 本部门数据
DATA_SCOPE_SELF = 4           # 仅本人数据
DATA_SCOPE_CUSTOM = 5         # 自定义部门数据


def get_data_scope(login_user: dict) -> int:
    """解析当前用户的有效数据权限范围：返回所有角色中范围最大（数值最小）的，ADMIN 固定为全部"""
    if is_admin(login_user):
        return DATA_SCOPE_ALL
    scopes = login_user.get("data_scopes", [])
    return min(scopes) if scopes else DATA_SCOPE_SELF


def get_custom_dept_ids(login_user: dict) -> list:
    """解析自定义数据权限（data_scope=5）的部门集合，跨角色取并集"""
    dept_ids = set()
    for scope in login_user.get("data_scope_dept_ids", []) or []:
        if scope:
            dept_ids.update(int(d) for d in scope.split(",") if d.strip().isdigit())
    return sorted(dept_ids)


async def get_child_dept_ids(session: AsyncSession, dept_id: int) -> list:
    """递归查询部门及其全部子孙部门 id，用于"本部门及以下"数据权限"""
    result = set()

    async def _collect(pid: int):
        rows = await session.execute(select(Dept.dept_id).where(
            Dept.parent_id == pid, Dept.is_deleted == 0, Dept.status == 1))
        for row in rows.scalars().all():
            if row not in result:
                result.add(row)
                await _collect(row)

    await _collect(dept_id)
    return sorted(result)


async def build_data_scope_filter(login_user: dict, owner_field, user_field=None,
                                  dept_field=None, session: AsyncSession = None):
    """
    构建数据权限 SQLAlchemy 过滤条件（与外部查询用 .where() 组合）：
    :param login_user: 登录用户载荷（需含 dept_id）
    :param owner_field: 数据归属人 user_id 字段（列对象，如 User.user_id）
    :param user_field: 可选，若数据有独立 user_id 字段且与 owner_field 不同则传
    :param dept_field: 数据所属部门 dept_id 字段（列对象，如 User.dept_id）
    :param session: 数据库会话，计算子孙部门时需要
    :return: SQLAlchemy 条件（无条件时返回 None）
    """
    from common.common_entity.rbac_entity import Dept  # 延迟导入避免循环依赖

    scope = get_data_scope(login_user)
    user_id = login_user.get("user_id")
    dept_id = login_user.get("dept_id") or 0
    target_user_field = user_field or owner_field

    # 1. 全部数据：ADMIN 或角色含"全部数据"权限直接放行
    if scope == DATA_SCOPE_ALL:
        return None

    # 5. 自定义部门数据
    if scope == DATA_SCOPE_CUSTOM:
        custom_ids = get_custom_dept_ids(login_user)
        if not dept_field or not custom_ids:
            return None
        return dept_field.in_(custom_ids)

    # 2. 本部门及以下
    if scope == DATA_SCOPE_DEPT_AND_CHILD:
        if not dept_field:
            return None
        child_ids = [dept_id] + (await get_child_dept_ids(session, dept_id) if session else [])
        return dept_field.in_(child_ids)

    # 3. 本部门数据
    if scope == DATA_SCOPE_DEPT:
        if not dept_field:
            return None
        return dept_field == dept_id

    # 4. 仅本人数据（默认）
    return target_user_field == user_id


# 延迟导入 Dept 到模块尾部，避免与 rbac_entity 形成循环导入
from common.common_entity.rbac_entity import Dept  # noqa: E402