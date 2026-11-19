# -*- coding: utf-8 -*-
"""团队模式编排（tb_orch_team）：CRUD + 搜索 + 查看 + 授权口径 + 运行时拓扑。

权限两层与单智能体完全同构（口径见计划 §1、orch_agent_service 顶部）：
- RBAC 只管「全局入口 + 页面准入」：创建/列表/授权入口 @has_permission 在 router；
- 行级按钮（查看/使用/编辑/删除/授权 share）全由 ACL 行内 actions 驱动；
- 验证 = 等价 edit：执行入口 ensure_any(use, edit)。资源编码 orch_team。

成员来源（计划 §6）：从 tb_orch_agent 克隆（sourceAgentId，逐成员 use 口径过滤）
或页面自定义；解析与落库见 orch_member_util.resolve_member。成员/队长配置只落
config_json 快照，绝不回写单智能体表（克隆语义）。

运行时（计划 §5 团队分支）：TeamPipeline(leader, [TeamMember(agent, description)])；
主 Agent 独立一份 state，子 Agent 各按 主session_id:index 独立一份（并发不能共用）。
runtime_config 产出流水线拓扑快照供 worker 的 orch_pipelines 构建器消费。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, select

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_DELETE, ACTION_EDIT, ACTION_USE, ACTION_VIEW, build_visible_cond,
    decorate_actions, ensure_action, ensure_action_strict, ensure_any,
)
from common.common_utils.creator_util import attach_creator
from service.service_harness.models.orch_agent import OrchTeam
from service.service_harness.schemas.orch_schema import OrchTeamSaveReq
from service.service_harness.services.orch_member_util import resolve_member

RESOURCE_CODE = "orch_team"
# 克隆来源候选：复用单智能体的可见集（action=view 下推），只回公开列
AGENT_RESOURCE_CODE = "orch_agent"


def _fmt_time(value) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class OrchTeamService:
    """团队模式编排服务（CRUD + 搜索 + 查看 + 授权 + 运行时拓扑）。"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(login_user: dict, page: int = 1, page_size: int = 10,
                   keyword: str = None, status: int = None) -> dict:
        async with mysql_client.get_session() as session:
            conds = [OrchTeam.is_deleted == 0]
            if keyword:
                like = f"%{keyword}%"
                conds.append(or_(OrchTeam.name.like(like), OrchTeam.purpose.like(like)))
            if status is not None:
                conds.append(OrchTeam.status == status)
            visible = build_visible_cond(login_user, RESOURCE_CODE, OrchTeam.id,
                                         OrchTeam.created_by)
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(OrchTeam).where(*conds))).scalar()
            rows = (await session.execute(
                select(OrchTeam).where(*conds)
                .order_by(OrchTeam.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [OrchTeamService._to_row(r) for r in rows]
            await decorate_actions(login_user, RESOURCE_CODE, items, session=session)
            await attach_creator(items, id_key="created_by", session=session)
            return {"total": total, "items": items}

    @staticmethod
    def _to_row(row: OrchTeam) -> dict:
        """列表行：不带 config_json 全文，member_count 供卡片概览。"""
        cfg = row.config_json or {}
        members = cfg.get("members") or []
        return {
            "id": row.id, "name": row.name, "purpose": row.purpose,
            "leaderName": (cfg.get("leader") or {}).get("name"),
            "memberCount": len(members), "status": row.status,
            "created_by": row.created_by, "updated_by": row.updated_by,
            "create_time": _fmt_time(row.create_time),
            "update_time": _fmt_time(row.update_time),
        }

    @staticmethod
    async def get_row(id_: int) -> Optional[OrchTeam]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(OrchTeam).where(OrchTeam.id == id_, OrchTeam.is_deleted == 0)
            )).scalar_one_or_none()

    @staticmethod
    async def detail(login_user: dict, id_: int) -> Optional[dict]:
        """查看：卡 ACL view；返回完整配置（config_json 里的队长/成员快照）供编辑表单。"""
        row = await OrchTeamService.get_row(id_)
        if not row:
            return None
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_VIEW,
                            owner_id=row.created_by)
        return {
            "id": row.id, "name": row.name, "purpose": row.purpose,
            "status": row.status, "config": row.config_json or {},
            "created_by": row.created_by,
            "create_time": _fmt_time(row.create_time),
            "update_time": _fmt_time(row.update_time),
        }

    # ==================== 新增 / 编辑 / 删除 ====================
    @staticmethod
    async def create(login_user: dict, body: OrchTeamSaveReq) -> int:
        """创建：入口权限（RBAC add）在 router 卡；这里解析成员 → 落库并记归属人。"""
        leader, members_json, config = await OrchTeamService._build_snapshots(login_user, body)
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = OrchTeam(name=body.name, purpose=body.purpose,
                           leader_agent_id=leader.get("source_agent_id"),
                           members=members_json, config_json=config,
                           status=1 if body.status else 0, created_by=user_id)
            session.add(row)
            await session.flush()
            new_id = row.id
            await session.commit()
            log.info("orch_team created: id={} name={} members={}",
                     new_id, body.name, len(members_json))
            return new_id

    @staticmethod
    async def update(login_user: dict, id_: int, body: OrchTeamSaveReq) -> bool:
        """编辑：卡 ACL edit；整体重建队长/成员快照（成员配置不回写单表）。"""
        row = await OrchTeamService.get_row(id_)
        if not row:
            raise ValueError("团队不存在")
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_EDIT,
                            owner_id=row.created_by)
        leader, members_json, config = await OrchTeamService._build_snapshots(login_user, body)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchTeam, id_)
            if obj is None or obj.is_deleted:
                raise ValueError("团队不存在")
            obj.name = body.name
            obj.purpose = body.purpose
            obj.leader_agent_id = leader.get("source_agent_id")
            obj.members = members_json
            obj.config_json = config
            obj.status = 1 if body.status else 0
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_team updated: id={}", id_)
            return True

    @staticmethod
    async def delete(login_user: dict, id_: int) -> bool:
        """删除：软删；卡 ACL 严格 delete（同部门不自动放开）。"""
        row = await OrchTeamService.get_row(id_)
        if not row:
            return False
        await ensure_action_strict(login_user, RESOURCE_CODE, id_, ACTION_DELETE)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchTeam, id_)
            if obj is None or obj.is_deleted:
                return False
            obj.is_deleted = 1
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_team deleted(soft): id={}", id_)
            return True

    @staticmethod
    async def _build_snapshots(login_user: dict, body: OrchTeamSaveReq):
        """解析队长/成员规格 → (leader快照, members轻量列, config_json完整快照)。"""
        leader = await resolve_member(login_user, body.leader)
        leader = OrchTeamService._ensure_named(leader, "leader")
        members_snap = []
        members_json = []
        for index, spec in enumerate(body.members):
            m = await resolve_member(login_user, spec)
            m = OrchTeamService._ensure_named(m, f"member_{index}")
            m["index"] = index
            members_snap.append(m)
            members_json.append({"agent_id": m.get("source_agent_id"),
                                 "index": index, "description": m.get("description")})
        config = {"leader": leader, "members": members_snap, "reset_members": True}
        return leader, members_json, config

    @staticmethod
    def _ensure_named(snap: dict, fallback: str) -> dict:
        """成员解析后 name 可能为空（克隆来源被删且未自定义）：给确定性兜底名。"""
        if not snap.get("name"):
            snap["name"] = fallback
        return snap

    @staticmethod
    async def clone_sources(login_user: dict) -> list[dict]:
        """克隆来源候选：当前用户可见的单智能体（id/name/purpose），供团队页「从单智能体克隆」。

        用 orch_agent 的 view 口径下推可见集（与单智能体列表同一判定）；不回敏感列。
        具体克隆预填走通用 orch_agent detail（ACL view，无 RBAC）。
        """
        from service.service_harness.models.orch_agent import OrchAgent
        async with mysql_client.get_session() as session:
            conds = [OrchAgent.is_deleted == 0]
            visible = build_visible_cond(login_user, AGENT_RESOURCE_CODE, OrchAgent.id,
                                         OrchAgent.created_by)
            if visible is not None:
                conds.append(visible)
            rows = (await session.execute(
                select(OrchAgent).where(*conds).order_by(OrchAgent.id.desc())
            )).scalars().all()
            return [{"id": r.id, "name": r.name, "purpose": r.purpose} for r in rows]

    # ==================== 运行时 / 验证台入口 ====================
    @staticmethod
    async def ensure_runnable(login_user: dict, config_id: int) -> None:
        """验证台执行入口（team）：卡 use 或 edit（能跑起来才能验证）。"""
        row = await OrchTeamService.get_row(config_id)
        if not row:
            raise ValueError("团队不存在")
        await ensure_any(login_user, RESOURCE_CODE, config_id, [ACTION_USE, ACTION_EDIT],
                         owner_id=row.created_by)

    @staticmethod
    async def runtime_config(config_id: int) -> Optional[dict]:
        """worker 运行时拓扑（不鉴权）：返回 TeamPipeline 构建器所需快照。"""
        row = await OrchTeamService.get_row(config_id)
        if not row:
            return None
        cfg = row.config_json or {}
        return {
            "mode": "team",
            "config_id": row.id,
            "leader": cfg.get("leader") or {},
            "members": cfg.get("members") or [],
            "reset_members": bool(cfg.get("reset_members", True)),
        }
