"""资源实例授权（tb_resource_acl）的唯一读写口。

职责边界（与 common/common_permission/resource_guard.py 一分为二）：
- resource_guard 只判「能不能」（三档并集：归属人 ∪ 数据范围 ∪ 显式授权），不写库；
- 本模块只负责「把授权事实写进去/读出来」，判定门槛时一律回调 resource_guard，
  绝不在这里重写一遍规则 —— 两处规则早晚分叉就是越权漏洞的起点。

四条必须守住的口径：
1. 授权与查看授权都要对该资源有 share：归属人 / ADMIN / 被显式授过 share 的人。
   用 ensure_action_strict（不看数据范围）：同部门能看能用 ≠ 能把权限转授出去。
   但 share 本身不可转授（只有归属人与 ADMIN 能授 share）：否则一条授权会自己长出子授权。
2. 唯一键 uk_acl_row 是六元组 (resource_code, resource_id, grantee_type, grantee_id,
   action, dept_include_sub)，且**不含 is_deleted**。所以「撤销后重新授权」不能直接 insert，
   必须复用那条软删行，否则撞唯一键报 500（下面按六元组建索引后 upsert）。
3. 整表提交（授权弹窗语义）按目标集合做差集：目标里没有的在册行软删，在册没有的目标行新增/复活。
   幂等：同一份 payload 连提两次结果一致。
4. 只登记显式授权。「我创建的」「部门内的」是隐式规则，由资源归属列现算，落库反而会在
   部门调整时留下脏数据。
"""
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_entity.rbac_entity import Dept, ResourceAcl, Role, UserGroup
from common.common_entity.user_entity import User
from common.common_exception.custom_exception import UnauthorizedException
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import build_data_scope_filter, is_admin
from common.common_permission.resource_guard import (
    ACTION_LABELS, ACTION_SHARE, GRANTEE_ALL, GRANTEE_DEPT, GRANTEE_GROUP, GRANTEE_LABELS,
    GRANTEE_ROLE, GRANTEE_USER, RESOURCE_SPECS, check_actions, ensure_action_strict,
    load_resource_brief, maybe_session, spec_of)

# 资源类型 → 功能权限点（页面准入用；数据权限另由 share 判定）。
# 权限点串已在 sql/v2_init.sql PART 8 建好，这里只做映射，不再新增第二份定义
GRANT_PERMISSIONS: dict[str, str] = {
    "knowledge_base": "ai:kb:grant",
    "document": "ai:doc:grant",
    "workflow": "workflow:workflow:grant",
    "workflow_template": "workflow:template:grant",
    "tool": "workflow:tool:grant",
    "skill": "workflow:skill:grant",
    "mcp": "workflow:mcp:grant",
}

# 单实例授权行上限：护栏而非业务参数（防脚本或误操作一次提交上万行），
# 计数口径是「主体 × 动作」，与落库行数一致
MAX_GRANTS_PER_RESOURCE = 200


class AclService:

    # ==================== 元信息（前端不硬编码动作码） ====================
    @staticmethod
    def resource_meta(resource_code: str = None) -> list[dict]:
        """下发资源类型的可授权动作清单与分组；resource_code 为空时下发全部已登记类型。

        弹窗勾选框、403 文案、SQL 注释三处的动作名同源于 RESOURCE_SPECS/ACTION_LABELS，
        前端拿到什么渲染什么，避免「后端加了动作、前端少个勾选项」这种静默漂移。
        groups 是「操作范围」那一列的分类（每个资源在 spec 里各写各的组），
        下发时就按组把名称一并带好，前端只补图标和排版，不再自己猜哪个动作算一类。
        """
        codes = [resource_code] if resource_code else list(RESOURCE_SPECS)
        out = []
        for code in codes:
            spec = spec_of(code)  # 未登记的编码直接 ValueError(400)，不返回空列表糊弄前端
            out.append({
                "resource_code": code,
                "resource_name": spec["name"],
                "grant_permission": GRANT_PERMISSIONS.get(code, ""),
                "actions": [{"code": a, "name": ACTION_LABELS.get(a, a)} for a in spec["actions"]],
                # 数据范围档（同部门自动获得的动作）：弹窗里给个提示，免得重复授权
                "scope_actions": [{"code": a, "name": ACTION_LABELS.get(a, a)}
                                  for a in spec.get("scope", [])],
                "groups": [{"key": g["key"], "name": g["name"], "hint": g.get("hint", ""),
                            "actions": [{"code": a, "name": ACTION_LABELS.get(a, a)}
                                        for a in g["actions"]]}
                           for g in (spec.get("groups") or [])],
                "grantee_types": [{"code": t, "name": n} for t, n in GRANTEE_LABELS.items()],
            })
        return out

    # ==================== 读 ====================
    @staticmethod
    async def list_grants(login_user: dict, resource_code: str, resource_id: Any) -> dict:
        """某条资源的在册授权，按授权主体分组（弹窗直接渲染）

        读侧同样要 share：授权行写着「谁被授了什么」，本身是敏感信息，
        不能让任意登录用户靠遍历 resource_id 打听到别人拿到过什么权限。
        返回带上资源摘要，弹窗标题与二次确认文案直接用，不必再调一次业务详情接口。
        """
        brief, _ = await AclService._require_share(login_user, resource_code, resource_id)
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(ResourceAcl).where(ResourceAcl.resource_code == brief["resource_code"],
                                          ResourceAcl.resource_id == brief["resource_id"],
                                          ResourceAcl.is_deleted == 0)
                .order_by(ResourceAcl.grantee_type, ResourceAcl.grantee_id,
                          ResourceAcl.action))).scalars().all()
            names = await AclService._grantee_names(
                session, [(r.grantee_type, r.grantee_id) for r in rows])

        groups: dict[tuple, dict] = {}
        for r in rows:
            key = (r.grantee_type, r.grantee_id, r.dept_include_sub)
            item = groups.setdefault(key, {
                "grantee_type": r.grantee_type,
                "grantee_type_name": GRANTEE_LABELS.get(r.grantee_type, "未知"),
                "grantee_id": r.grantee_id,
                "grantee_name": names.get((r.grantee_type, r.grantee_id), ""),
                "dept_include_sub": r.dept_include_sub,
                "expire_time": r.expire_time,
                "actions": []})
            item["actions"].append({"acl_id": r.acl_id, "action": r.action,
                                    "action_name": ACTION_LABELS.get(r.action, r.action)})
        return {"resource": brief, "items": list(groups.values())}

    @staticmethod
    async def grantee_options(grantee_type: int, keyword: str = None, limit: int = 20,
                              login_user: dict = None) -> list[dict]:
        """授权主体候选（用户/角色/部门/用户组）。全员不用检索，固定返回一条。

        用户候选按数据范围过滤：能授权不等于能翻全公司通讯录；按 id 直填仍然支持，
        保存时会重新校验存在性，所以这里少给不会造成越权，只会影响可发现性。
        """
        limit = max(1, min(int(limit or 20), 100))
        like = f"%{keyword}%" if keyword else None
        async with mysql_client.get_session() as session:
            if grantee_type == GRANTEE_USER:
                query = select(User.user_id, User.username, User.real_name, Dept.dept_name) \
                    .outerjoin(Dept, Dept.dept_id == User.dept_id) \
                    .where(User.is_deleted == 0)
                if login_user is not None:
                    scope_filter = build_data_scope_filter(login_user, User.created_by)
                    if scope_filter is not None:
                        query = query.where(scope_filter)
                if like:
                    query = query.where(or_(User.username.like(like), User.real_name.like(like)))
                rows = await session.execute(query.order_by(User.user_id).limit(limit))
                return [{"grantee_id": r[0], "grantee_name": r[2] or r[1],
                         "label": "{}（{}）{}".format(r[2] or r[1], r[1], f" · {r[3]}" if r[3] else "")}
                        for r in rows.all()]
            if grantee_type == GRANTEE_ROLE:
                query = select(Role.role_id, Role.role_name, Role.role_code) \
                    .where(Role.is_deleted == 0, Role.status == 1)
                if like:
                    query = query.where(or_(Role.role_name.like(like), Role.role_code.like(like)))
                rows = await session.execute(query.order_by(Role.role_id).limit(limit))
                return [{"grantee_id": r[0], "grantee_name": r[1], "label": f"{r[1]}（{r[2]}）"}
                        for r in rows.all()]
            if grantee_type == GRANTEE_DEPT:
                query = select(Dept.dept_id, Dept.dept_name) \
                    .where(Dept.is_deleted == 0, Dept.status == 1)
                if like:
                    query = query.where(Dept.dept_name.like(like))
                rows = await session.execute(query.order_by(Dept.dept_id).limit(limit))
                return [{"grantee_id": r[0], "grantee_name": r[1], "label": r[1]}
                        for r in rows.all()]
            if grantee_type == GRANTEE_GROUP:
                query = select(UserGroup.group_id, UserGroup.group_name, UserGroup.description) \
                    .where(UserGroup.is_deleted == 0, UserGroup.status == 1)
                if like:
                    query = query.where(UserGroup.group_name.like(like))
                rows = await session.execute(query.order_by(UserGroup.group_id).limit(limit))
                return [{"grantee_id": r[0], "grantee_name": r[1],
                         "label": r[1] + (f" · {r[2]}" if r[2] else "")} for r in rows.all()]
            if grantee_type == GRANTEE_ALL:
                return [{"grantee_id": 0, "grantee_name": "全员", "label": "全员"}]
        raise ValueError(f"不支持的授权主体类型：{grantee_type}")

    # ==================== 写 ====================
    @staticmethod
    async def save_grants(login_user: dict, resource_code: str, resource_id: Any,
                          grants: Sequence[Any]) -> dict:
        """整表提交（授权弹窗「保存」）：目标集合之外的在册授权一律撤销"""
        return await AclService._apply(login_user, resource_code, resource_id, grants,
                                       replace=True)

    @staticmethod
    async def add_grants(login_user: dict, resource_code: str, resource_id: Any,
                         grants: Sequence[Any]) -> dict:
        """增量追加（不动在册行）：开放接口/批量补授权时用，免得先拉全集再整表回传"""
        return await AclService._apply(login_user, resource_code, resource_id, grants,
                                       replace=False)

    @staticmethod
    async def _apply(login_user: dict, resource_code: str, resource_id: Any,
                     grants: Sequence[Any], *, replace: bool) -> dict:
        """授权落库的公共主体：校验 → 展开 → 按六元组 upsert（replace=True 时附带撤销差集）

        grants 元素字段：grantee_type / grantee_id / dept_include_sub / expire_time / actions。
        同一主体重复出现时动作取并集；同一动作给了不同过期时间时以最后一次为准
        （不做「整条覆盖」那种让前端难以解释的语义）。
        返回 {saved, revoked, skipped, resource}，前端据此提示即可，不必再拉一次列表。
        """
        brief, owner_id = await AclService._require_share(login_user, resource_code, resource_id)
        # 转授限制：share 只能由归属人或 ADMIN 授出。被授了 share 的人可以再授 view/use，
        # 但不能把「授权权」继续传下去 —— 否则一条授权能自己长出子授权，审计追不到头
        can_delegate = is_admin(login_user) or owner_id == int(login_user.get("user_id") or 0)
        targets, skipped = AclService._expand(grants or [], brief, owner_id, can_delegate)
        # 主体存在性一次批量校验：不存在/已停用/已删除的主体授出去是永远命中不了的死行
        pairs = list({(k[0], k[1]) for k in targets})
        known = await AclService._grantee_names(None, pairs)
        for (gtype, gid, _sub, _action) in targets:
            if gtype != GRANTEE_ALL and (gtype, gid) not in known:
                raise ValueError(f"{GRANTEE_LABELS.get(gtype, '该类型')}不存在或已停用/删除"
                                 f"（id={gid}）")

        operator = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            # 连软删行一起取：唯一键不认 is_deleted，只按在册行做差集一定会撞键
            current = (await session.execute(
                select(ResourceAcl).where(ResourceAcl.resource_code == brief["resource_code"],
                                          ResourceAcl.resource_id == brief["resource_id"]))
                        ).scalars().all()
            by_key = {(r.grantee_type, r.grantee_id, r.dept_include_sub, r.action): r
                      for r in current}
            # 上限在事务内按「提交后应有的行数」判：整表覆盖就是目标集本身，
            # 追加还要算上在册行（只判本次入参会让反复追加绕过护栏）
            active_before = {k for k, r in by_key.items() if r.is_deleted == 0}
            projected = set(targets) if replace else (active_before | set(targets))
            if len(projected) > MAX_GRANTS_PER_RESOURCE:
                raise ValueError(f"单条资源最多 {MAX_GRANTS_PER_RESOURCE} 个「主体×动作」授权，"
                                 f"本次提交后会有 {len(projected)} 个")
            saved = revoked = 0
            for key, payload in targets.items():
                row = by_key.get(key)
                if row is None:
                    session.add(ResourceAcl(
                        resource_code=brief["resource_code"], resource_id=brief["resource_id"],
                        grantee_type=key[0], grantee_id=key[1], dept_include_sub=key[2],
                        action=key[3], expire_time=payload,
                        is_deleted=0, create_by=operator, update_by=operator))
                else:
                    # 复活或改档：同一个六元组始终只有一行，重复授权不产生新行
                    row.is_deleted = 0
                    row.expire_time = payload
                    row.update_by = operator
                saved += 1
            for key, row in by_key.items():
                if replace and row.is_deleted == 0 and key not in targets:
                    row.is_deleted = 1
                    row.update_by = operator
                    revoked += 1
            await session.commit()
        log.info(f"ACL saved: {brief['resource_code']}#{brief['resource_id']} by {operator} "
                 f"upsert={saved} revoke={revoked} skip={len(skipped)} replace={replace}")
        return {"saved": saved, "revoked": revoked, "skipped": skipped, "resource": brief}

    @staticmethod
    async def revoke_grants(login_user: dict, resource_code: str, resource_id: Any,
                            grantee_type: int, grantee_id: int = 0,
                            actions: Sequence[str] = None) -> int:
        """撤销授权：不传 actions 即撤销该主体在这条资源上的全部动作（移除整行授权主体的快捷口）"""
        brief, _ = await AclService._require_share(login_user, resource_code, resource_id)
        spec = spec_of(brief["resource_code"])
        codes = check_actions(brief["resource_code"], actions) if actions else list(spec["actions"])
        gtype = int(grantee_type)
        gid = 0 if gtype == GRANTEE_ALL else int(grantee_id or 0)
        operator = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            result = await session.execute(
                update(ResourceAcl)
                .where(ResourceAcl.resource_code == brief["resource_code"],
                       ResourceAcl.resource_id == brief["resource_id"],
                       ResourceAcl.grantee_type == gtype,
                       ResourceAcl.grantee_id == gid,
                       ResourceAcl.action.in_(codes),
                       ResourceAcl.is_deleted == 0)
                .values(is_deleted=1, update_by=operator))
            await session.commit()
        revoked = int(result.rowcount or 0)
        log.info(f"ACL revoked: {brief['resource_code']}#{brief['resource_id']} "
                 f"grantee={gtype}/{gid} rows={revoked} by {operator}")
        return revoked

    # ==================== 内部 ====================
    @staticmethod
    async def _require_share(login_user: dict, resource_code: str,
                             resource_id: Any) -> tuple[dict, int]:
        """确认资源还在 + 我对它有 share；返回 (brief, owner_id)

        归属人与 ADMIN 的放行都在 ensure_action_strict 里，这里不重复实现；
        代价是这条低频弹窗链路多一次归属回表，换「判定规则只有一份」这笔账划算。
        """
        brief, owner_id = await load_resource_brief(resource_code, resource_id)
        await ensure_action_strict(login_user, brief["resource_code"],
                                   brief["resource_id"], ACTION_SHARE)
        return brief, owner_id

    @staticmethod
    def _expand(grants: Sequence[Any], brief: dict, owner_id: int,
                can_delegate: bool) -> tuple[dict, list[dict]]:
        """校验入参并展开成 {(grantee_type, grantee_id, dept_include_sub, action): expire_time}

        expire_time 挂在「主体×动作」上：接口入参按主体给（一组动作 + 一个过期时间），
        所以同主体的所有动作共用该值。

        跳过（而非报错）的两种噪声输入：
        - 给归属人自己授权：归属人本就全权，落库也是永远用不上的死行；
        - 非部门主体带 dept_include_sub：归零处理，否则同一主体会因这个位裂成两行。
        报错的：非法动作码、缺 grantee_id、过期时间不晚于当前、非归属人转授 share。
        """
        targets: dict[tuple, Any] = {}
        skipped: list[dict] = []
        for g in grants:
            gtype = int(g.grantee_type)
            if gtype == GRANTEE_ALL:
                gid, include_sub = 0, 0
            else:
                gid = int(g.grantee_id or 0)
                if gid <= 0:
                    raise ValueError(f"{GRANTEE_LABELS.get(gtype, gtype)}授权必须指定 grantee_id")
                # 含下级只对部门有意义，其余主体一律归零，否则同一主体会因这个位裂成两行
                include_sub = int(g.dept_include_sub or 0) if gtype == GRANTEE_DEPT else 0
                if gtype == GRANTEE_USER and gid == owner_id:
                    skipped.append({"grantee_type": gtype, "grantee_id": gid,
                                    "reason": "归属人本身已拥有全部权限，无需授权"})
                    continue
            expire = getattr(g, "expire_time", None)
            if expire is not None and expire <= datetime.now():
                raise ValueError(f"过期时间必须晚于当前时间（grantee={gtype}/{gid}）")
            for action in check_actions(brief["resource_code"], g.actions):
                if action == ACTION_SHARE and not can_delegate:
                    raise UnauthorizedException("只有归属人或管理员可以转授「授权」动作")
                targets[(gtype, gid, include_sub, action)] = expire
        return targets, skipped

    @staticmethod
    async def _grantee_names(session: Optional[AsyncSession],
                             pairs: Sequence[tuple]) -> dict[tuple, str]:
        """批量回显授权主体名称，同时充当存在性校验的唯一数据来源。

        每类主体的「有效」口径与登录载荷里的保持一致（见 login_service._build_payload）：
        角色/部门/用户组要 status=1，用户只看 is_deleted=0（停用用户仍是有身份的自然人，
        授权行留着无害，等它重新启用即生效；停用组/停用角色不参与鉴权，故要求启用）。
        """
        grouped: dict[int, set] = {}
        for gtype, gid in pairs:
            if int(gtype) == GRANTEE_ALL:
                continue
            grouped.setdefault(int(gtype), set()).add(int(gid))
        async with maybe_session(session) as s:
            names: dict[tuple, str] = {}
            if grouped.get(GRANTEE_USER):
                rows = await s.execute(select(User.user_id, User.username, User.real_name)
                                       .where(User.user_id.in_(grouped[GRANTEE_USER]),
                                              User.is_deleted == 0))
                for uid, username, real_name in rows.all():
                    names[(GRANTEE_USER, uid)] = real_name or username
            if grouped.get(GRANTEE_ROLE):
                rows = await s.execute(select(Role.role_id, Role.role_name)
                                       .where(Role.role_id.in_(grouped[GRANTEE_ROLE]),
                                              Role.is_deleted == 0, Role.status == 1))
                for rid, role_name in rows.all():
                    names[(GRANTEE_ROLE, rid)] = role_name
            if grouped.get(GRANTEE_DEPT):
                rows = await s.execute(select(Dept.dept_id, Dept.dept_name)
                                       .where(Dept.dept_id.in_(grouped[GRANTEE_DEPT]),
                                              Dept.is_deleted == 0, Dept.status == 1))
                for did, dept_name in rows.all():
                    names[(GRANTEE_DEPT, did)] = dept_name
            if grouped.get(GRANTEE_GROUP):
                rows = await s.execute(select(UserGroup.group_id, UserGroup.group_name)
                                       .where(UserGroup.group_id.in_(grouped[GRANTEE_GROUP]),
                                              UserGroup.is_deleted == 0, UserGroup.status == 1))
                for gid_, group_name in rows.all():
                    names[(GRANTEE_GROUP, gid_)] = group_name
        return names
