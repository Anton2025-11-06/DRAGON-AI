import base64
from datetime import datetime

from fastapi import Request
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from common.common_mysql.mysql import mysql_client
from common.common_log.log_init import log
from common.common_permission.permission import (
    get_login_user, build_data_scope_filter, is_admin, DATA_SCOPE_ALL, DATA_SCOPE_SELF)
from common.common_utils.base32hex import b32hexdecode, b32hexencode

from service.service_system.models.user import User
from service.service_system.schemas.user_schema import (
    UserCreateRequest, UserUpdateRequest, ResetPasswordRequest)
from service.service_system.services.rbac_service import RbacService
from common.common_entity.rbac_entity import Dept, Role, UserRole


class UserService:

    # ==================== 查询 ====================
    @staticmethod
    async def get_user_by_id(user_id: int):
        async with mysql_client.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id, User.is_deleted == 0)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_username(username: str):
        async with mysql_client.get_session() as session:
            result = await session.execute(
                select(User).where(User.username == username, User.is_deleted == 0)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def list_users(request: Request = None, page: int = 1, page_size: int = 10,
                         username: str = None, status: int = None):
        """
        用户分页列表（企业级数据权限）：
        - ADMIN：查看全部
        - 其他角色：按数据权限范围过滤（全部/本部门及以下/本部门/仅本人/自定义部门）
        返回项附带部门名称与角色集合
        """
        async with mysql_client.get_session() as session:
            query = select(User).where(User.is_deleted == 0)
            # 应用数据权限过滤（非 ADMIN 时生成过滤条件）
            if request is not None:
                login_user = await get_login_user(request)
                if not is_admin(login_user):
                    scope_filter = await build_data_scope_filter(
                        login_user, User.user_id, dept_field=User.dept_id, session=session)
                    if scope_filter is not None:
                        query = query.where(scope_filter)

            if username:
                query = query.where(User.username.like(f"%{username}%"))
            if status is not None:
                query = query.where(User.status == status)

            count_query = select(func.count()).select_from(query.subquery())
            total = (await session.execute(count_query)).scalar()

            rows = (await session.execute(
                query.order_by(User.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()

            # 批量补充部门名称与角色
            dept_ids = {u.dept_id for u in rows if u.dept_id}
            depts = {}
            if dept_ids:
                dept_rows = await session.execute(select(Dept.dept_id, Dept.dept_name).where(Dept.dept_id.in_(dept_ids)))
                depts = {d[0]: d[1] for d in dept_rows.all()}
            role_map = await UserService._batch_user_roles(session, [u.user_id for u in rows])

            items = []
            for u in rows:
                item = {
                    "user_id": u.user_id, "username": u.username, "real_name": u.real_name,
                    "email": u.email, "phone": u.phone, "dept_id": u.dept_id or 0,
                    "dept_name": depts.get(u.dept_id), "status": u.status,
                    "roles": role_map.get(u.user_id, []),
                    "create_time": u.create_time, "update_time": u.update_time,
                }
                items.append(item)
            return {"total": total, "items": items}

    @staticmethod
    async def _batch_user_roles(session: AsyncSession, user_ids: list) -> dict:
        """批量查询用户的角色集合，返回 {user_id: [{role_id, role_code, role_name}]}"""
        if not user_ids:
            return {}
        rows = await session.execute(
            select(UserRole.user_id, Role.role_id, Role.role_code, Role.role_name)
            .join(Role, Role.role_id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids), Role.is_deleted == 0, Role.status == 1))
        role_map = {}
        for user_id, role_id, role_code, role_name in rows.all():
            role_map.setdefault(user_id, []).append(
                {"role_id": role_id, "role_code": role_code, "role_name": role_name})
        return role_map

    # ==================== 新增 ====================
    @staticmethod
    async def create_user(request: UserCreateRequest):
        """创建用户：支持指定部门与初始角色；真实姓名/部门/角色/手机号/邮箱为必填项"""
        missing = [name for name, val in (("真实姓名", request.real_name), ("部门", request.dept_id),
                                           ("角色", request.role_ids), ("手机号", request.phone),
                                           ("邮箱", request.email)) if not val]
        if missing:
            raise ValueError(f"必填信息缺失：{', '.join(missing)}")
        async with mysql_client.get_session() as session:
            try:
                user = User(
                    username=request.username,
                    password=b32hexencode(request.password),
                    email=request.email,
                    phone=request.phone,
                    real_name=request.real_name,
                    dept_id=request.dept_id or 0,
                )
                session.add(user)
                await session.flush()

                # 分配初始角色：显式传入则按传入分配，否则回退默认 USER 角色
                role_ids = request.role_ids if request.role_ids is not None else []
                if not role_ids:
                    role_row = await session.execute(
                        select(Role.role_id).where(Role.role_code == "USER", Role.is_deleted == 0))
                    default_role = role_row.scalar_one_or_none()
                    if default_role:
                        role_ids = [default_role]
                for role_id in role_ids:
                    session.add(UserRole(user_id=user.user_id, role_id=role_id))

                await session.commit()
                log.info(f"User created: {request.username}, roles={role_ids}")
                return True
            except IntegrityError as e:
                await session.rollback()
                log.error(f"User creation failed: {str(e)}")
                return False

    # ==================== 更新 ====================
    @staticmethod
    async def update_user(user_id: int, request: UserUpdateRequest):
        async with mysql_client.get_session() as session:
            try:
                result = await session.execute(
                    select(User).where(User.user_id == user_id, User.is_deleted == 0)
                )
                user = result.scalar_one_or_none()
                if not user:
                    raise ValueError("用户不存在")

                update_data = {}
                if request.username:
                    update_data["username"] = request.username
                if request.password:
                    update_data["password"] = b32hexencode(request.password)
                if request.email is not None:
                    update_data["email"] = request.email
                if request.phone is not None:
                    update_data["phone"] = request.phone
                if request.real_name is not None:
                    update_data["real_name"] = request.real_name
                if request.dept_id is not None:
                    update_data["dept_id"] = request.dept_id
                if request.status is not None:
                    update_data["status"] = request.status

                if update_data:
                    await session.execute(
                        update(User).where(User.user_id == user_id).values(**update_data)
                    )
                    await session.commit()
                log.info(f"Updated user: {user_id}")
                return True
            except IntegrityError:
                await session.rollback()
                raise ValueError("用户名或邮箱已存在")
            except Exception as e:
                await session.rollback()
                log.error(f"User update failed: {str(e)}")
                raise Exception("操作失败")

    # ==================== 删除 ====================
    @staticmethod
    async def delete_user(user_id: int):
        """逻辑删除用户，并解绑角色关系；禁止删除超级管理员与当前登录用户自己"""
        async with mysql_client.get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id, User.is_deleted == 0)
            )
            user = result.scalar_one_or_none()
            if not user:
                raise ValueError("用户不存在")

            # 内置超管保护：禁止删除
            role_row = await session.execute(
                select(Role.role_id).join(UserRole, UserRole.role_id == Role.role_id)
                .where(UserRole.user_id == user_id, Role.role_code == "ADMIN")
            )
            if role_row.scalar_one_or_none():
                raise ValueError("超级管理员不允许删除")

            await session.execute(
                update(User).where(User.user_id == user_id).values(is_deleted=1)
            )
            await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
            await session.commit()
            log.info(f"Deleted user: {user_id}")
            return True

    # ==================== 密码 ====================
    @staticmethod
    async def reset_password(user_id: int, request: ResetPasswordRequest):
        """管理员重置指定用户密码（同时清除其登录态，强制重新登录）"""
        async with mysql_client.get_session() as session:
            user = (await session.execute(
                select(User).where(User.user_id == user_id, User.is_deleted == 0))).scalar_one_or_none()
            if not user:
                raise ValueError("用户不存在")
            await session.execute(
                update(User).where(User.user_id == user_id)
                .values(password=b32hexencode(request.password)))
            await session.commit()
            log.info(f"Password reset by admin, user_id={user_id}")
            return True

    @staticmethod
    async def verify_password(plain_password: str, user_id: int) -> bool:
        """校验用户密码是否正确（个人中心改密前验证原密码）"""
        async with mysql_client.get_session() as session:
            result = await session.execute(select(User).where(User.user_id == user_id, User.is_deleted == 0))
            user = result.scalar_one_or_none()
        return user and user.password == b32hexencode(plain_password)

    @staticmethod
    async def change_own_password(request: Request, old_password: str, new_password: str):
        """当前登录用户修改自己的密码"""
        login_user = await get_login_user(request)
        if not await UserService.verify_password(old_password, login_user["user_id"]):
            raise ValueError("原密码输入错误")
        await UserService.reset_password(login_user["user_id"], ResetPasswordRequest(password=new_password))
        return True

    # ==================== 用户中心 ====================
    @staticmethod
    async def me(request: Request) -> dict:
        """
        当前登录用户信息聚合：用户资料 + 角色 + 权限标识 + 菜单树（按用户权限过滤）
        menus 只包含当前用户可见的菜单，前端据此渲染导航
        """
        login_user = await get_login_user(request)
        user = await UserService.get_user_by_id(login_user["user_id"])
        user_vo = {
            "user_id": user.user_id, "username": user.username, "real_name": user.real_name,
            "email": user.email, "phone": user.phone, "dept_id": user.dept_id or 0, "status": user.status,
            # 部门显示名（个人信息/头像展示）
            "dept_name": await RbacService.dept_name(user.dept_id) if user and user.dept_id else None,
        } if user else {}
        return {
            "user": user_vo,
            "roles": login_user.get("roles", []),
            # 角色显示名（按 code 顺序与 roles 对齐），供前端头像/个人信息展示
            "role_names": await UserService._user_role_names(login_user["user_id"]),
            "permissions": login_user.get("permissions", []),
            "menus": await RbacService.user_menu_tree(login_user["user_id"]),
        }

    @staticmethod
    async def _user_role_names(user_id: int) -> list:
        """查询用户角色的显示名（启用状态）"""
        async with mysql_client.get_session() as session:
            rows = await session.execute(
                select(Role.role_name).join(UserRole, UserRole.role_id == Role.role_id)
                .where(UserRole.user_id == user_id, Role.is_deleted == 0, Role.status == 1))
            return [r[0] for r in rows.all()]