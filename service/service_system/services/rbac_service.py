from sqlalchemy import delete, func, select, update

from common.common_entity.rbac_entity import Dept, Menu, Role, RoleMenu, UserRole
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_system.schemas.rbac_schema import (
    DeptCreateRequest, DeptUpdateRequest, MenuCreateRequest, MenuUpdateRequest,
    RoleCreateRequest, RoleMenusRequest, RoleUpdateRequest)


class RbacService:

    # ==================== 角色 ====================
    @staticmethod
    async def list_roles(page: int = 1, page_size: int = 10, role_name: str = None,
                         status: int = None):
        async with mysql_client.get_session() as session:
            query = select(Role).where(Role.is_deleted == 0)
            if role_name:
                query = query.where(Role.role_name.like(f"%{role_name}%"))
            if status is not None:
                query = query.where(Role.status == status)
            total = (await session.execute(
                select(func.count()).select_from(query.subquery()))).scalar()
            rows = (await session.execute(
                query.order_by(Role.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()
            items = [{
                "role_id": r.role_id, "role_code": r.role_code, "role_name": r.role_name,
                "description": r.description, "data_scope": r.data_scope,
                "dept_ids": r.dept_ids, "is_builtin": r.is_builtin, "status": r.status,
                "create_time": r.create_time, "update_time": r.update_time,
            } for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def list_all_roles() -> list:
        """全部启用角色（下拉框使用）"""
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(Role).where(Role.is_deleted == 0, Role.status == 1)
                .order_by(Role.role_id.asc()))).scalars().all()
            return [{"role_id": r.role_id, "role_code": r.role_code, "role_name": r.role_name,
                     "data_scope": r.data_scope} for r in rows]

    @staticmethod
    async def create_role(request: RoleCreateRequest):
        async with mysql_client.get_session() as session:
            exists = await session.execute(
                select(Role).where(Role.role_code == request.role_code, Role.is_deleted == 0))
            if exists.scalar_one_or_none():
                raise ValueError("角色编码已存在")
            session.add(Role(**request.model_dump()))
            await session.commit()
            log.info(f"Role created: {request.role_code}")
            return True

    @staticmethod
    async def update_role(role_id: int, request: RoleUpdateRequest):
        async with mysql_client.get_session() as session:
            role = (await session.execute(
                select(Role).where(Role.role_id == role_id, Role.is_deleted == 0))).scalar_one_or_none()
            if not role:
                raise ValueError("角色不存在")
            # 内置角色仅允许修改名称/描述/状态，不允许修改数据权限范围
            data = {k: v for k, v in request.model_dump().items() if v is not None}
            if role.is_builtin:
                data.pop("data_scope", None)
                data.pop("dept_ids", None)
            if data:
                await session.execute(update(Role).where(Role.role_id == role_id).values(**data))
                await session.commit()
            return True

    @staticmethod
    async def delete_role(role_id: int):
        """删除角色：内置角色受保护；删除时解绑用户-角色、角色-菜单关系"""
        async with mysql_client.get_session() as session:
            role = (await session.execute(
                select(Role).where(Role.role_id == role_id, Role.is_deleted == 0))).scalar_one_or_none()
            if not role:
                raise ValueError("角色不存在")
            if role.is_builtin:
                raise ValueError("内置角色不允许删除")
            await session.execute(update(Role).where(Role.role_id == role_id).values(is_deleted=1))
            await session.execute(delete(UserRole).where(UserRole.role_id == role_id))
            await session.execute(delete(RoleMenu).where(RoleMenu.role_id == role_id))
            await session.commit()
            log.info(f"Role deleted: {role_id}")
            return True

    @staticmethod
    async def assign_menus(role_id: int, request: RoleMenusRequest):
        """为角色分配菜单/按钮权限（先清后插）"""
        async with mysql_client.get_session() as session:
            await session.execute(delete(RoleMenu).where(RoleMenu.role_id == role_id))
            for menu_id in request.menu_ids:
                session.add(RoleMenu(role_id=role_id, menu_id=menu_id))
            await session.commit()
            log.info(f"Role {role_id} menus assigned: {request.menu_ids}")
            return True

    @staticmethod
    async def role_menus(role_id: int) -> list:
        async with mysql_client.get_session() as session:
            rows = await session.execute(
                select(RoleMenu.menu_id).where(RoleMenu.role_id == role_id))
            return [r[0] for r in rows.all()]

    # ==================== 菜单/权限 ====================
    @staticmethod
    async def menu_tree() -> list:
        """管理端全量菜单树（含按钮权限点），供菜单管理/角色授权使用"""
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(Menu).where(Menu.is_deleted == 0)
                .order_by(Menu.sort.asc(), Menu.menu_id.asc()))).scalars().all()
        return _build_tree([m for m in rows if m.parent_id == 0], list(rows))

    @staticmethod
    async def user_menu_tree(user_id: int) -> list:
        """当前用户可见菜单树：由其角色关联的菜单构建（ADMIN 返回全量）"""
        async with mysql_client.get_session() as session:
            # 判断是否 ADMIN
            admin_row = await session.execute(
                select(Role.role_id).join(UserRole, UserRole.role_id == Role.role_id)
                .where(UserRole.user_id == user_id, Role.role_code == "ADMIN",
                       Role.is_deleted == 0, Role.status == 1))
            is_admin_user = admin_row.scalar_one_or_none() is not None

            if is_admin_user:
                rows = (await session.execute(
                    select(Menu).where(Menu.is_deleted == 0, Menu.status == 1, Menu.visible == 1)
                    .order_by(Menu.sort.asc(), Menu.menu_id.asc()))).scalars().all()
            else:
                # 用户角色 → 角色菜单 → 菜单（去重）
                rows = (await session.execute(
                    select(Menu)
                    .join(RoleMenu, RoleMenu.menu_id == Menu.menu_id)
                    .join(Role, Role.role_id == RoleMenu.role_id)
                    .join(UserRole, UserRole.role_id == Role.role_id)
                    .where(UserRole.user_id == user_id,
                           Role.is_deleted == 0, Role.status == 1,
                           Menu.is_deleted == 0, Menu.status == 1, Menu.visible == 1)
                    .order_by(Menu.sort.asc(), Menu.menu_id.asc())
                    .distinct())).scalars().all()
        return _build_tree([m for m in rows if m.parent_id == 0], list(rows))

    @staticmethod
    async def create_menu(request: MenuCreateRequest):
        async with mysql_client.get_session() as session:
            session.add(Menu(**request.model_dump()))
            await session.commit()
            log.info(f"Menu created: {request.menu_name}")
            return True

    @staticmethod
    async def update_menu(menu_id: int, request: MenuUpdateRequest):
        async with mysql_client.get_session() as session:
            data = {k: v for k, v in request.model_dump().items() if v is not None}
            if data:
                await session.execute(update(Menu).where(Menu.menu_id == menu_id).values(**data))
                await session.commit()
            return True

    @staticmethod
    async def delete_menu(menu_id: int):
        """删除菜单：存在子菜单时禁止删除；删除后解绑角色关系"""
        async with mysql_client.get_session() as session:
            children = (await session.execute(
                select(Menu).where(Menu.parent_id == menu_id, Menu.is_deleted == 0))).scalars().all()
            if children:
                raise ValueError("存在子菜单，无法删除")
            await session.execute(update(Menu).where(Menu.menu_id == menu_id).values(is_deleted=1))
            await session.execute(delete(RoleMenu).where(RoleMenu.menu_id == menu_id))
            await session.commit()
            log.info(f"Menu deleted: {menu_id}")
            return True

    # ==================== 用户授权 ====================
    @staticmethod
    async def assign_roles(user_id: int, role_ids: list):
        async with mysql_client.get_session() as session:
            await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
            for role_id in role_ids:
                session.add(UserRole(user_id=user_id, role_id=role_id))
            await session.commit()
            log.info(f"User {user_id} roles assigned: {role_ids}")
            return True

    @staticmethod
    async def user_roles(user_id: int) -> list:
        async with mysql_client.get_session() as session:
            rows = await session.execute(
                select(Role.role_id, Role.role_code, Role.role_name)
                .join(UserRole, UserRole.role_id == Role.role_id)
                .where(UserRole.user_id == user_id, Role.is_deleted == 0, Role.status == 1))
            return [{"role_id": r[0], "role_code": r[1], "role_name": r[2]} for r in rows.all()]

    # ==================== 部门（数据权限组织单元） ====================
    @staticmethod
    async def dept_tree() -> list:
        """部门树"""
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(Dept).where(Dept.is_deleted == 0)
                .order_by(Dept.sort.asc(), Dept.dept_id.asc()))).scalars().all()
        return _build_dept_tree([d for d in rows if d.parent_id == 0], list(rows))

    @staticmethod
    async def dept_name(dept_id: int) -> str:
        """根据部门ID查询部门显示名（供 me 接口返回）"""
        async with mysql_client.get_session() as session:
            row = await session.execute(
                select(Dept.dept_name).where(Dept.dept_id == dept_id, Dept.is_deleted == 0))
            return row.scalar_one_or_none()

    @staticmethod
    async def create_dept(request: DeptCreateRequest):
        async with mysql_client.get_session() as session:
            session.add(Dept(**request.model_dump()))
            await session.commit()
            log.info(f"Dept created: {request.dept_name}")
            return True

    @staticmethod
    async def update_dept(dept_id: int, request: DeptUpdateRequest):
        async with mysql_client.get_session() as session:
            data = {k: v for k, v in request.model_dump().items() if v is not None}
            if data:
                await session.execute(update(Dept).where(Dept.dept_id == dept_id).values(**data))
                await session.commit()
            return True

    @staticmethod
    async def delete_dept(dept_id: int):
        """删除部门：存在子部门或部门下存在用户时禁止删除"""
        async with mysql_client.get_session() as session:
            dept = (await session.execute(
                select(Dept).where(Dept.dept_id == dept_id, Dept.is_deleted == 0))).scalar_one_or_none()
            if not dept:
                raise ValueError("部门不存在")
            children = (await session.execute(
                select(Dept.dept_id).where(Dept.parent_id == dept_id, Dept.is_deleted == 0))).scalars().all()
            if children:
                raise ValueError("存在子部门，无法删除")
            from service.service_system.models.user import User  # 局部导入避免循环依赖
            user_count = (await session.execute(
                select(func.count()).select_from(User).where(User.dept_id == dept_id, User.is_deleted == 0))).scalar()
            if user_count:
                raise ValueError("部门下存在用户，无法删除")
            await session.execute(update(Dept).where(Dept.dept_id == dept_id).values(is_deleted=1))
            await session.commit()
            log.info(f"Dept deleted: {dept_id}")
            return True


def _build_tree(parents: list, all_nodes: list) -> list:
    """菜单层级树构建：children_map 按 parent_id 分组后递归组装"""
    children_map = {}
    for node in all_nodes:
        children_map.setdefault(node.parent_id, []).append(node)
    return [_menu_vo(p, children_map) for p in parents]


def _menu_vo(menu: Menu, children_map: dict) -> dict:
    vo = {"menu_id": menu.menu_id, "parent_id": menu.parent_id, "menu_name": menu.menu_name,
          "menu_type": menu.menu_type, "path": menu.path, "component": menu.component,
          "perm": menu.perm, "icon": menu.icon, "sort": menu.sort,
          "visible": menu.visible, "status": menu.status}
    vo["children"] = [_menu_vo(c, children_map) for c in children_map.get(menu.menu_id, [])]
    return vo


def _build_dept_tree(parents: list, all_nodes: list) -> list:
    children_map = {}
    for node in all_nodes:
        children_map.setdefault(node.parent_id, []).append(node)
    return [_dept_vo(p, children_map) for p in parents]


def _dept_vo(dept: Dept, children_map: dict) -> dict:
    vo = {"dept_id": dept.dept_id, "parent_id": dept.parent_id, "dept_name": dept.dept_name,
          "sort": dept.sort, "status": dept.status}
    vo["children"] = [_dept_vo(c, children_map) for c in children_map.get(dept.dept_id, [])]
    return vo