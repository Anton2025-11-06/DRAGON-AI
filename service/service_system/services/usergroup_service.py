"""用户组（tb_user_group / tb_user_group_member）读写。

定位：用户组只是「资源 ACL 的一类授权主体」，给谁授权、能做什么由 acl_service 判定；
本模块只管组的组织事实（名称、启停、成员），因此：
- 组本身只走功能权限 system:usergroup:*，不参与资源级鉴权；
- 组成员变化不改任何授权行：授权指向 group_id，成员进出的效果就是登录载荷里
  group_ids 的增减（下次登录生效，与角色/部门同口径）；
- 删除组必须连带撤销指向它的授权行，否则 tb_resource_acl 会留下指向已删主体的野行，
  那些行永远命中不了（判定侧按主体 id 关联），却会让授权列表回显出一堆空名字。
- 组是创建人的私有对象：列表只列自己创建的，改/删/看成员/设成员四个口一律卡归属
  （ADMIN 豁免）。只收紧列表不收紧写口等于没收紧 —— group_id 是自增的，随便猜一个
  就能翻别人的成员名单。可见范围与 /acl/grantees 的组候选同源（见 acl_service）。
"""
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from common.common_entity.rbac_entity import (
    Dept, ResourceAcl, Role, UserGroup, UserGroupMember, UserRole)
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import is_admin
from common.common_permission.resource_guard import GRANTEE_GROUP, GRANTEE_USER
from common.common_log.log_init import log


class UserGroupService:

    # ==================== 查询 ====================
    @staticmethod
    async def list_groups(page: int = 1, page_size: int = 10, group_name: str = None,
                          status: int = None, login_user: dict = None):
        """用户组分页列表：只列自己创建的（ADMIN 看全部）

        附带成员数（一次 group by 批量取，不逐行数），供列表页直接展示。

        这里刻意不走 build_data_scope_filter：那套口径会把「同部门同事创建的」也放进来，
        而用户组定位是个人协作分组，组成员名单本身就是「谁在跟谁合作项目」的信息，
        不该出现在同事的列表里。数据范围那套并集口径留给业务资源实例（resource_guard）。
        """
        async with mysql_client.get_session() as session:
            query = select(UserGroup).where(UserGroup.is_deleted == 0)
            if login_user is not None and not is_admin(login_user):
                query = query.where(
                    UserGroup.created_by == int(login_user.get("user_id") or 0))
            if group_name:
                query = query.where(UserGroup.group_name.like(f"%{group_name}%"))
            if status is not None:
                query = query.where(UserGroup.status == status)

            total = (await session.execute(
                select(func.count()).select_from(query.subquery()))).scalar()
            rows = (await session.execute(
                query.order_by(UserGroup.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()

            counts = {}
            if rows:
                count_rows = await session.execute(
                    select(UserGroupMember.group_id, func.count())
                    .where(UserGroupMember.group_id.in_([g.group_id for g in rows]))
                    .group_by(UserGroupMember.group_id))
                counts = {g: c for g, c in count_rows.all()}

            items = [{"group_id": g.group_id, "group_name": g.group_name,
                      "description": g.description, "status": g.status,
                      "member_count": counts.get(g.group_id, 0),
                      "created_by": g.created_by,
                      "create_time": g.create_time, "update_time": g.update_time}
                     for g in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def list_all_groups(login_user: dict = None) -> list:
        """启用中的用户组（下拉框/成员维护等页内控件用）：非 ADMIN 只出自己创建的

        与 list_groups 同口径：「组不共享」不能只收紧列表页、却在 /user-groups/all
        这个下拉口上放开，否则别人建的组照样在页内被列全名（ACL 授权弹窗的组
        候选同理，见 acl_service.grantee_options）。
        """
        async with mysql_client.get_session() as session:
            query = (select(UserGroup.group_id, UserGroup.group_name, UserGroup.description)
                     .where(UserGroup.is_deleted == 0, UserGroup.status == 1))
            if login_user is not None and not is_admin(login_user):
                query = query.where(
                    UserGroup.created_by == int(login_user.get("user_id") or 0))
            rows = await session.execute(query.order_by(UserGroup.group_name))
            return [{"group_id": g, "group_name": n, "description": d} for g, n, d in rows.all()]

    @staticmethod
    async def list_members(group_id: int, login_user: dict):
        """组成员列表（含部门名，供成员维护表格直接展示）：只给看自己创建的组"""
        async with mysql_client.get_session() as session:
            await UserGroupService._get_group_or_raise(session, group_id, login_user)
            rows = await session.execute(
                select(UserGroupMember.user_id, UserGroupMember.create_time)
                .where(UserGroupMember.group_id == group_id)
                .order_by(UserGroupMember.create_time))
            pairs = rows.all()
            user_ids = [p[0] for p in pairs]
            info = {}
            if user_ids:
                from common.common_entity.user_entity import User  # 延迟导入避免循环
                user_rows = await session.execute(
                    select(User.user_id, User.username, User.real_name, User.dept_id,
                           Dept.dept_name, User.status)
                    .outerjoin(Dept, Dept.dept_id == User.dept_id)
                    .where(User.user_id.in_(user_ids)))
                info = {r[0]: {"user_id": r[0], "username": r[1], "real_name": r[2],
                               "dept_id": r[3] or 0, "dept_name": r[4], "status": r[5]}
                        for r in user_rows.all()}
            # 用户被删掉时成员行是脏数据（删除用户未清理组），回显成占位项而不是整行消失
            items = [info.get(uid) or {"user_id": uid, "username": f"已删除用户({uid})",
                                       "real_name": None, "dept_id": 0, "dept_name": None,
                                       "status": 0}
                     for uid, _ in pairs]
            return items

    @staticmethod
    async def member_candidates(keyword: str = None, limit: int = 100,
                               login_user: dict = None) -> list:
        """成员候选用户（按数据范围过滤）：供用户组页的选人控件用。

        默认给到 100 条（grantee_options 那一侧的上限也就是 100）：选人控件开弹时
        先递一批可点的，而不是空列表等关键字 —— 0 条和「坏了」在界面上分不开。

        为什么不自己写一份 select(User...)：「这个人能看到哪些用户」的口径
        （is_deleted=0 + 数据范围 + 藏管理员账号 + 姓名/账号/部门检索）在
        AclService.grantee_options 里已经定了，两处各查一遍早晚分叉；也不让前端直接
        调 /users 列表：那个接口卡的是 system:user:list，有成员维护权的人不该被
        通讯录权限卡住。
        """
        from service.service_system.services.acl_service import AclService  # 延迟导入避开相互引用

        rows = await AclService.grantee_options(GRANTEE_USER, keyword, limit, login_user=login_user)
        # 逐列下发姓名/账号/部门：前端拼选项标签不该再从 label 里回拆字串
        return [{"user_id": r["grantee_id"], "username": r.get("username"),
                 "real_name": r.get("real_name"), "dept_name": r.get("dept_name"),
                 "label": r["label"]} for r in rows]

    # ==================== 写入 ====================
    @staticmethod
    async def create_group(request, creator_id: int = 0):
        """新建用户组：名称全局唯一（唯一键 uk_group_name 不区分软删，故连已删行一起判）"""
        async with mysql_client.get_session() as session:
            if await UserGroupService._name_taken(session, request.group_name):
                raise ValueError("用户组名称已存在（含已删除的用户组，请换个名称）")
            session.add(UserGroup(group_name=request.group_name,
                                  description=request.description,
                                  status=request.status,
                                  created_by=creator_id or 0))
            try:
                await session.commit()
            except IntegrityError as e:
                # 并发双击创建会穿过上面的判重撞唯一键，这里必须转成可读文案而非 500
                await session.rollback()
                log.error(f"UserGroup create conflict: {str(e)}")
                raise ValueError("用户组名称已存在（含已删除的用户组，请换个名称）")
            log.info(f"UserGroup created: {request.group_name}")
            return True

    @staticmethod
    async def update_group(group_id: int, request, login_user: dict):
        """更新用户组（名称/描述/状态）；停用组即从成员的 group_ids 里消失（登录载荷按 status=1 过滤）"""
        async with mysql_client.get_session() as session:
            group = await UserGroupService._get_group_or_raise(session, group_id, login_user)
            data = {k: v for k, v in request.model_dump().items() if v is not None}
            if "group_name" in data and data["group_name"] != group.group_name:
                if await UserGroupService._name_taken(session, data["group_name"], exclude_id=group_id):
                    raise ValueError("用户组名称已存在（含已删除的用户组，请换个名称）")
            if data:
                await session.execute(update(UserGroup).where(UserGroup.group_id == group_id)
                                      .values(**data))
                await session.commit()
            return True

    @staticmethod
    async def delete_group(group_id: int, login_user: dict):
        """删除用户组：软删组 + 清成员 + 撤销指向该组的授权行（三件事必须同事务）"""
        operator_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            await UserGroupService._get_group_or_raise(session, group_id, login_user)
            await session.execute(update(UserGroup).where(UserGroup.group_id == group_id)
                                  .values(is_deleted=1))
            await session.execute(delete(UserGroupMember).where(UserGroupMember.group_id == group_id))
            revoked = (await session.execute(
                update(ResourceAcl)
                .where(ResourceAcl.grantee_type == GRANTEE_GROUP,
                       ResourceAcl.grantee_id == group_id,
                       ResourceAcl.is_deleted == 0)
                .values(is_deleted=1, update_by=operator_id))).rowcount
            await session.commit()
            log.info(f"UserGroup deleted: {group_id}, revoked {revoked} acl rows")
            return {"revoked_acl": revoked or 0}

    @staticmethod
    async def assign_members(group_id: int, user_ids: list, login_user: dict):
        """整表重设成员（先清后插，与角色分配菜单同一套语义，前端一次提交即最终状态）

        传入的用户必须存在且未删除：把不存在的 id 静默丢掉会让用户以为「已经加进去了」，
        而实际授权（指向该组）对它不生效，这种偏差比直接报错更糟。
        """
        wanted = sorted({int(u) for u in (user_ids or []) if int(u or 0) > 0})
        operator_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            await UserGroupService._get_group_or_raise(session, group_id, login_user)
            if wanted and not is_admin(login_user):
                # 候选列表已经把管理员账号藏起来了，写入侧必须再挡一次：否则手造一个
                # user_ids=[管理员] 就能把「看不见的人」塞进组，再靠上面的成员列表
                # 把他的姓名/账号/部门读出来 —— 只藏可见性而不堵回显，等于没藏
                admin_ids = set((await session.execute(
                    select(UserRole.user_id)
                    .join(Role, Role.role_id == UserRole.role_id)
                    .where(UserRole.user_id.in_(wanted), Role.role_code == "ADMIN",
                           Role.is_deleted == 0, Role.status == 1))).scalars().all())
                if admin_ids:
                    raise ValueError("不能把系统管理员加入用户组")
            if wanted:
                from common.common_entity.user_entity import User
                exist_ids = set((await session.execute(
                    select(User.user_id).where(User.user_id.in_(wanted),
                                               User.is_deleted == 0))).scalars().all())
                missing = [u for u in wanted if u not in exist_ids]
                if missing:
                    raise ValueError(f"这些用户不存在或已删除：{missing}")
            await session.execute(delete(UserGroupMember).where(UserGroupMember.group_id == group_id))
            for uid in wanted:
                session.add(UserGroupMember(group_id=group_id, user_id=uid))
            await session.commit()
            log.info(f"UserGroup {group_id} members set by {operator_id}: {wanted}")
            return {"member_count": len(wanted)}

    # ==================== 内部工具 ====================
    @staticmethod
    def _assert_owner(group, login_user: dict) -> None:
        """组只归创建人（ADMIN 豁免）：与列表口径必须同源，否则会出现「看得到却改不动」或反过来

        确认不了操作人就拒绝，不放行：宁可报得严，不能把「没拿到登录态」当成「不受限制」。
        存量数据里 created_by=0 的老组因此只有 ADMIN 能改，需要交回个人时由管理员处理。
        """
        uid = int((login_user or {}).get("user_id") or 0)
        if uid <= 0:
            raise ValueError("无法确认操作人身份，请重新登录后重试")
        if is_admin(login_user) or int(group.created_by or 0) == uid:
            return
        raise ValueError("只能操作自己创建的用户组")

    @staticmethod
    async def _get_group_or_raise(session, group_id: int, login_user: dict):
        """取组 + 归属闸门：四个按 group_id 寻址的口都走这里，漏一处就是猜 id 越权"""
        group = (await session.execute(
            select(UserGroup).where(UserGroup.group_id == group_id,
                                    UserGroup.is_deleted == 0))).scalar_one_or_none()
        if not group:
            raise ValueError("用户组不存在")
        UserGroupService._assert_owner(group, login_user)
        return group

    @staticmethod
    async def _name_taken(session, group_name: str, exclude_id: int = 0) -> bool:
        """名称占用判断：不带 is_deleted 条件 —— 唯一键本身不区分软删，判重口径必须一致"""
        query = select(UserGroup.group_id).where(UserGroup.group_name == group_name)
        if exclude_id:
            query = query.where(UserGroup.group_id != exclude_id)
        return (await session.execute(query.limit(1))).first() is not None
