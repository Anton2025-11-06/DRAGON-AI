from functools import wraps
from typing import Optional

from fastapi import Request
from sqlalchemy import or_, select
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


async def get_user_id(request: Request) -> int:
    try:
        login_user = await get_login_user(request)
        return int(login_user.get("user_id") or 0)
    except Exception:  # noqa: BLE001
        return 0  # 直连/旁路调用（网关未注入用户）时降级为系统用户


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
            # 流程执行权限校验：X-Workflow-Token 存在则 bypass, workflow api-key调用
            if request.headers.__contains__("X-Workflow-Token"):
                return await func(request, *args, **kwargs)
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
            # 流程执行权限校验：X-Workflow-Token 存在则 bypass, workflow api-key调用
            if request.headers.__contains__("X-Workflow-Token"):
                return await func(request, *args, **kwargs)

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
DATA_SCOPE_ALL = 1  # 全部数据
DATA_SCOPE_DEPT_AND_CHILD = 2  # 本部门及以下
DATA_SCOPE_DEPT = 3  # 本部门数据
DATA_SCOPE_SELF = 4  # 仅本人数据


async def get_child_dept_ids(session: AsyncSession, dept_id: int) -> list:
    """递归查询部门的全部子孙部门 id（不含自身），供登录时展开数据权限部门集合"""
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


def get_login_scope(login_user: dict):
    """取登录载荷里预算好的数据权限部门集合 scope_dept_ids：
    - None：不限（管理员或 data_scope=1 全部数据）
    - []：仅本人（data_scope=4）
    - 非空列表：这些部门（已含子部门）内用户创建的数据可见
    该字段由 LoginService 登录时按各角色 data_scope 解析并缓存进载荷，查询端不再递归。
    """
    if is_admin(login_user):
        return None
    if "scope_dept_ids" not in login_user:
        # 旧登录态未预算该字段：保守回退为仅本人，待重新登录后按部门放开
        return []
    return login_user.get("scope_dept_ids")


def build_data_scope_filter(login_user: dict, owner_col):
    """构建数据权限过滤条件（与外部查询用 .where() 组合），返回 None 表示不限制。
    可见口径：我创建的 OR 创建人落在 scope_dept_ids 部门内的（两个集合的并集，与当前用户所属部门无关）。
    :param owner_col: 数据归属人列（如 X.created_by）
    """
    scope_dept_ids = get_login_scope(login_user)
    if scope_dept_ids is None:
        return None
    conds = [owner_col == login_user.get("user_id")]
    if scope_dept_ids:
        from common.common_entity.user_entity import User  # 延迟导入避免循环依赖
        conds.append(owner_col.in_(
            select(User.user_id).where(User.dept_id.in_(scope_dept_ids))))
    return or_(*conds)


# 延迟导入 Dept 到模块尾部，避免与 rbac_entity 形成循环导入
from common.common_entity.rbac_entity import Dept  # noqa: E402
