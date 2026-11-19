# -*- coding: utf-8 -*-
"""SOP 模式编排（tb_orch_sop）：CRUD + 搜索 + 查看 + 授权口径 + 运行时拓扑。

权限两层与单/团队/目标同构（口径见计划 §1）：RBAC 管入口（list/add/grant），行级按钮
全由 ACL actions 驱动；验证=等价 edit（ensure_any(use, edit)）。资源编码 orch_sop。

流程节点（计划 §3）：每节点 名称/流程定义/执行者/检查者/最大驳回次数（默认 3），执行者
与检查者从 tb_orch_agent 克隆或自定义（resolve_member，use 口径过滤）。steps 存轻量引用列、
config_json 存完整快照、flow_order 存顺序快照（数组序即流程顺序）；成员配置不回写单表。

运行时 SOPEngine(SOP(steps=[SOPStep(subject, description, executor, verifier, max_attempts)]))：
流程所有节点共用一份 state（顺序执行语义，计划 §5），workspace 共用。
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
from service.service_harness.models.orch_agent import OrchSop
from service.service_harness.schemas.orch_schema import OrchSopSaveReq
from service.service_harness.services.orch_member_util import resolve_member

RESOURCE_CODE = "orch_sop"
AGENT_RESOURCE_CODE = "orch_agent"


def _fmt_time(value) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class OrchSopService:
    """SOP 模式编排服务（CRUD + 搜索 + 查看 + 授权 + 运行时拓扑）。"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(login_user: dict, page: int = 1, page_size: int = 10,
                   keyword: str = None, status: int = None) -> dict:
        async with mysql_client.get_session() as session:
            conds = [OrchSop.is_deleted == 0]
            if keyword:
                like = f"%{keyword}%"
                conds.append(or_(OrchSop.name.like(like), OrchSop.purpose.like(like)))
            if status is not None:
                conds.append(OrchSop.status == status)
            visible = build_visible_cond(login_user, RESOURCE_CODE, OrchSop.id,
                                         OrchSop.created_by)
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(OrchSop).where(*conds))).scalar()
            rows = (await session.execute(
                select(OrchSop).where(*conds)
                .order_by(OrchSop.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [OrchSopService._to_row(r) for r in rows]
            await decorate_actions(login_user, RESOURCE_CODE, items, session=session)
            await attach_creator(items, id_key="created_by", session=session)
            return {"total": total, "items": items}

    @staticmethod
    def _to_row(row: OrchSop) -> dict:
        steps = row.config_json.get("steps") if row.config_json else None
        steps = steps or []
        return {
            "id": row.id, "name": row.name, "purpose": row.purpose,
            "stepCount": len(steps),
            "firstStep": (steps[0].get("subject") if steps else None),
            "status": row.status,
            "created_by": row.created_by, "updated_by": row.updated_by,
            "create_time": _fmt_time(row.create_time),
            "update_time": _fmt_time(row.update_time),
        }

    @staticmethod
    async def get_row(id_: int) -> Optional[OrchSop]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(OrchSop).where(OrchSop.id == id_, OrchSop.is_deleted == 0)
            )).scalar_one_or_none()

    @staticmethod
    async def detail(login_user: dict, id_: int) -> Optional[dict]:
        row = await OrchSopService.get_row(id_)
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
    async def create(login_user: dict, body: OrchSopSaveReq) -> int:
        steps_json, flow_order, config = await OrchSopService._build_snapshots(login_user, body)
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = OrchSop(name=body.name, purpose=body.purpose, steps=steps_json,
                          flow_order=flow_order, config_json=config,
                          status=1 if body.status else 0, created_by=user_id)
            session.add(row)
            await session.flush()
            new_id = row.id
            await session.commit()
            log.info("orch_sop created: id={} name={} steps={}",
                     new_id, body.name, len(steps_json))
            return new_id

    @staticmethod
    async def update(login_user: dict, id_: int, body: OrchSopSaveReq) -> bool:
        row = await OrchSopService.get_row(id_)
        if not row:
            raise ValueError("SOP 不存在")
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_EDIT,
                            owner_id=row.created_by)
        steps_json, flow_order, config = await OrchSopService._build_snapshots(login_user, body)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchSop, id_)
            if obj is None or obj.is_deleted:
                raise ValueError("SOP 不存在")
            obj.name = body.name
            obj.purpose = body.purpose
            obj.steps = steps_json
            obj.flow_order = flow_order
            obj.config_json = config
            obj.status = 1 if body.status else 0
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_sop updated: id={}", id_)
            return True

    @staticmethod
    async def delete(login_user: dict, id_: int) -> bool:
        row = await OrchSopService.get_row(id_)
        if not row:
            return False
        await ensure_action_strict(login_user, RESOURCE_CODE, id_, ACTION_DELETE)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchSop, id_)
            if obj is None or obj.is_deleted:
                return False
            obj.is_deleted = 1
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_sop deleted(soft): id={}", id_)
            return True

    @staticmethod
    async def _build_snapshots(login_user: dict, body: OrchSopSaveReq):
        """解析节点 → (steps轻量列, flow_order顺序列, config_json完整快照)。"""
        steps_json = []
        config_steps = []
        for spec in body.steps:
            executor = await resolve_member(login_user, spec.executor)
            executor["name"] = executor.get("name") or "executor"
            verifier = None
            if spec.verifier is not None:
                verifier = await resolve_member(login_user, spec.verifier)
                verifier["name"] = verifier.get("name") or "verifier"
            subject = spec.subject or "step"
            steps_json.append({
                "name": subject, "description": spec.description,
                "executor_agent_id": executor.get("source_agent_id"),
                "verifier_agent_id": (verifier or {}).get("source_agent_id"),
                "max_attempts": spec.maxAttempts,
            })
            config_steps.append({
                "subject": subject, "description": spec.description or "",
                "max_attempts": spec.maxAttempts,
                "executor": executor, "verifier": verifier,
            })
        flow_order = [s["name"] for s in steps_json]
        config = {"name": body.name, "steps": config_steps}
        return steps_json, flow_order, config

    @staticmethod
    async def clone_sources(login_user: dict) -> list[dict]:
        """克隆来源候选：当前用户可见的单智能体（节点执行者/检查者下拉）。"""
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
        row = await OrchSopService.get_row(config_id)
        if not row:
            raise ValueError("SOP 不存在")
        await ensure_any(login_user, RESOURCE_CODE, config_id, [ACTION_USE, ACTION_EDIT],
                         owner_id=row.created_by)

    @staticmethod
    async def runtime_config(config_id: int) -> Optional[dict]:
        """worker 运行时拓扑（不鉴权）：SOPEngine 构建器所需快照。"""
        row = await OrchSopService.get_row(config_id)
        if not row:
            return None
        cfg = row.config_json or {}
        return {
            "mode": "sop",
            "config_id": row.id,
            "name": cfg.get("name") or row.name,
            "steps": cfg.get("steps") or [],
        }
