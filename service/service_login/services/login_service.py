from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import time
import uuid

from common.common_constants.constant import PREFIX_LOGIN, TOKEN_EXPIRE
from common.common_entity.rbac_entity import Dept, Menu, Role, RoleMenu, UserRole
from common.common_log.log_init import log
from common.common_middleware.exception_handler import UnauthorizedException
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import (
    get_child_dept_ids, DATA_SCOPE_ALL, DATA_SCOPE_DEPT_AND_CHILD, DATA_SCOPE_DEPT,
    DATA_SCOPE_SELF)
from common.common_redis.redis import client
from common.common_utils.base32hex import b32hexencode
from common.common_utils.jwt_util import create_access_token, decode_token
from service.service_login.models import User
from service.service_login.schemas.user_schema import Login, UserCreateRequest


class LoginService:

    @staticmethod
    async def login(request: Login):
        """
        用户登录（JWT 认证）：
        1. 校验用户名/密码/账号状态
        2. 聚合用户基础信息 + 角色集合 + 菜单权限标识 + 数据权限范围
        3. 签发 JWT（载荷含 jti），并将载荷同步写入 Redis（login_{jti}）：
           中间件先验 JWT 签名/有效期，再验 Redis 登录态，支持登出强制失效
        """
        async with mysql_client.get_session() as session:
            result = await session.execute(select(User).where(User.username == request.username))
            user = result.scalar_one_or_none()
            if user is None:
                raise UnauthorizedException("账号或密码错误！")
            input_password = b32hexencode(request.password)
            if input_password != user.password:
                raise UnauthorizedException("账号或密码错误！")
            if user.status == 0:
                raise UnauthorizedException("账号已被禁用！")

            payload = await LoginService._build_payload(session, user)
            # 手动注入 jti（作为 Redis 登录态键），再签发 JWT；
            # 注意 create_access_token 只把 jti 写入 token 内部，不会回写 payload
            payload["jti"] = uuid.uuid4().hex
            # 同步注入整数秒时间戳，供“在线用户”列表展示登录/过期时间
            payload["iat"] = int(time.time())
            payload["exp"] = int(time.time()) + TOKEN_EXPIRE
            token = create_access_token(payload)
            await client.set(PREFIX_LOGIN + payload["jti"], payload, TOKEN_EXPIRE)
            return {"token": token, "user": payload, "expire": TOKEN_EXPIRE}

    @staticmethod
    async def _build_payload(session: AsyncSession, user: User) -> dict:
        """
        组装登录载荷：用户基础信息 + 角色集合 + 权限标识集合 + 数据权限范围
        - permissions：所有角色菜单授权中的按钮权限标识（perm 非空）
        - data_scopes：各角色的数据权限范围（数据权限过滤按最大范围放行）
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
        scope_dept_ids = await LoginService._resolve_scope_dept_ids(session, user, "ADMIN" in roles)
        return {
            "user_id": user.user_id, "username": user.username,
            "real_name": user.real_name, "dept_id": user.dept_id or 0,
            "dept_name": dept_name,
            "roles": sorted(roles), "permissions": sorted(perms),
            "data_scopes": sorted(data_scopes),
            "scope_dept_ids": scope_dept_ids,
        }

    @staticmethod
    async def _resolve_scope_dept_ids(session: AsyncSession, user: User, is_admin_role: bool):
        """按用户各角色 data_scope 解析可见部门集合（含子部门），多角色取并集：
        - 管理员或任一角色为“全部”(1) → None（不限）
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
            if scope == DATA_SCOPE_ALL:
                return None
            if scope == DATA_SCOPE_DEPT_AND_CHILD and own_dept:
                dept_ids.add(own_dept)
                dept_ids.update(await get_child_dept_ids(session, own_dept))
            elif scope == DATA_SCOPE_DEPT and own_dept:
                dept_ids.add(own_dept)
        return sorted(dept_ids)

    @staticmethod
    async def register(request: UserCreateRequest):
        """用户注册，默认分配 USER 角色"""
        async with mysql_client.get_session() as session:
            exists = await session.execute(
                select(User).where(User.username == request.username, User.is_deleted == 0))
            if exists.scalar_one_or_none():
                raise ValueError("用户名已存在！")
            try:
                user = User(
                    username=request.username,
                    password=b32hexencode(request.password),
                    email=request.email,
                    phone=request.phone,
                    real_name=request.real_name,
                )
                session.add(user)
                await session.flush()
                role_row = await session.execute(
                    select(Role).where(Role.role_code == "USER", Role.is_deleted == 0))
                default_role = role_row.scalar_one_or_none()
                if default_role:
                    session.add(UserRole(user_id=user.user_id, role_id=default_role.role_id))
                await session.commit()
                log.info(f"User registered: {request.username}")
                return True
            except IntegrityError:
                await session.rollback()
                raise ValueError("用户名已存在！")
            except Exception as e:
                await session.rollback()
                log.error(f"Register failed: {str(e)}")
                raise Exception("注册失败")

    @staticmethod
    async def logout(token: str):
        """
        用户登出：
        - JWT 格式：删除 login_{jti} 登录态，使该 token 立即失效（即使 JWT 未过期）
        - 兼容旧格式：直接删除 login_{token}
        """
        payload = decode_token(token)
        redis_key = PREFIX_LOGIN + (payload.get("jti") if payload and payload.get("jti") else token)
        if not await client.exists(redis_key):
            raise UnauthorizedException("非法操作！")
        await client.delete(redis_key)
        log.info("User logout success")

    @staticmethod
    async def refresh_token(token: str) -> dict:
        """
        Token 刷新：校验旧 token 与 Redis 登录态后换发新 JWT（滑动续期）
        旧登录态被删除，防止旧 token 继续使用
        """
        payload = decode_token(token)
        if not payload:
            raise UnauthorizedException("登录已失效，请重新登录！")
        jti = payload.get("jti")
        if not await client.exists(PREFIX_LOGIN + (jti or token)):
            raise UnauthorizedException("登录已失效，请重新登录！")

        # 删除旧登录态并签发新 token（同样需手动注入新的 jti）
        await client.delete(PREFIX_LOGIN + (jti or token))
        fresh_payload = {k: v for k, v in payload.items() if k not in ("iat", "exp", "jti")}
        fresh_payload["jti"] = uuid.uuid4().hex
        fresh_payload["iat"] = int(time.time())
        fresh_payload["exp"] = int(time.time()) + TOKEN_EXPIRE
        new_token = create_access_token(fresh_payload)
        await client.set(PREFIX_LOGIN + fresh_payload["jti"], fresh_payload, TOKEN_EXPIRE)
        return {"token": new_token, "user": fresh_payload, "expire": TOKEN_EXPIRE}