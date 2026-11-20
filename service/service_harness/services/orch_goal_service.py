# -*- coding: utf-8 -*-
"""目标模式编排（tb_orch_goal）：CRUD + 搜索 + 查看 + 授权口径 + 运行时拓扑。

权限两层与单/团队同构（口径见计划 §1）：RBAC 管入口（list/add/grant），行级按钮全由
ACL actions 驱动；验证=等价 edit（ensure_any(use, edit)）。资源编码 orch_goal。

成员来源：执行者/检查者各一个，从 tb_orch_agent 克隆或自定义（resolve_member，use
口径过滤），落 config_json 快照不回写单表。运行时 GoalPipeline(executor, verifier)：
executor↔verifier 循环到 pass/impossible；二者共用一份 state（顺序执行语义，计划 §5）。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_DELETE, ACTION_EDIT, ACTION_USE, ACTION_VIEW, build_visible_cond,
    decorate_actions, ensure_action, ensure_action_strict, ensure_any,
)
from common.common_utils.creator_util import attach_creator
from service.service_harness.models.orch_agent import OrchGoal
from service.service_harness.schemas.orch_schema import OrchGoalSaveReq
from service.service_harness.services.orch_member_util import resolve_member

RESOURCE_CODE = "orch_goal"
AGENT_RESOURCE_CODE = "orch_agent"


def _fmt_time(value) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class OrchGoalService:
    """目标模式编排服务（CRUD + 搜索 + 查看 + 授权 + 运行时拓扑）。"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(login_user: dict, page: int = 1, page_size: int = 10,
                   name: str = None, purpose: str = None,
                   status: int = None) -> dict:
        """分页 + 搜索（名称、用途两个独立条件，各自 like，同时填即取交集）。"""
        async with mysql_client.get_session() as session:
            conds = [OrchGoal.is_deleted == 0]
            if name:
                conds.append(OrchGoal.name.like(f"%{name}%"))
            if purpose:
                conds.append(OrchGoal.purpose.like(f"%{purpose}%"))
            if status is not None:
                conds.append(OrchGoal.status == status)
            visible = build_visible_cond(login_user, RESOURCE_CODE, OrchGoal.id,
                                         OrchGoal.created_by)
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(OrchGoal).where(*conds))).scalar()
            rows = (await session.execute(
                select(OrchGoal).where(*conds)
                .order_by(OrchGoal.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [OrchGoalService._to_row(r) for r in rows]
            await decorate_actions(login_user, RESOURCE_CODE, items, session=session)
            await attach_creator(items, id_key="created_by", session=session)
            return {"total": total, "items": items}

    @staticmethod
    def _to_row(row: OrchGoal) -> dict:
        cfg = row.config_json or {}
        return {
            "id": row.id, "name": row.name, "purpose": row.purpose,
            "executorName": (cfg.get("executor") or {}).get("name"),
            "verifierName": (cfg.get("verifier") or {}).get("name"),
            "status": row.status,
            "created_by": row.created_by, "updated_by": row.updated_by,
            "create_time": _fmt_time(row.create_time),
            "update_time": _fmt_time(row.update_time),
        }

    @staticmethod
    async def get_row(id_: int) -> Optional[OrchGoal]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(OrchGoal).where(OrchGoal.id == id_, OrchGoal.is_deleted == 0)
            )).scalar_one_or_none()

    @staticmethod
    async def detail(login_user: dict, id_: int) -> Optional[dict]:
        row = await OrchGoalService.get_row(id_)
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
    async def create(login_user: dict, body: OrchGoalSaveReq) -> int:
        executor, verifier, config = await OrchGoalService._build_snapshots(login_user, body)
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = OrchGoal(name=body.name, purpose=body.purpose,
                           executor_agent_id=executor.get("source_agent_id"),
                           verifier_agent_id=verifier.get("source_agent_id"),
                           config_json=config, status=1 if body.status else 0,
                           created_by=user_id)
            session.add(row)
            await session.flush()
            new_id = row.id
            await session.commit()
            log.info("orch_goal created: id={} name={}", new_id, body.name)
            return new_id

    @staticmethod
    async def update(login_user: dict, id_: int, body: OrchGoalSaveReq) -> bool:
        row = await OrchGoalService.get_row(id_)
        if not row:
            raise ValueError("目标不存在")
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_EDIT,
                            owner_id=row.created_by)
        executor, verifier, config = await OrchGoalService._build_snapshots(login_user, body)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchGoal, id_)
            if obj is None or obj.is_deleted:
                raise ValueError("目标不存在")
            obj.name = body.name
            obj.purpose = body.purpose
            obj.executor_agent_id = executor.get("source_agent_id")
            obj.verifier_agent_id = verifier.get("source_agent_id")
            obj.config_json = config
            obj.status = 1 if body.status else 0
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_goal updated: id={}", id_)
            return True

    @staticmethod
    async def delete(login_user: dict, id_: int) -> bool:
        row = await OrchGoalService.get_row(id_)
        if not row:
            return False
        await ensure_action_strict(login_user, RESOURCE_CODE, id_, ACTION_DELETE)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchGoal, id_)
            if obj is None or obj.is_deleted:
                return False
            obj.is_deleted = 1
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_goal deleted(soft): id={}", id_)
            return True

    @staticmethod
    async def _build_snapshots(login_user: dict, body: OrchGoalSaveReq):
        """解析执行者/检查者 → (executor快照, verifier快照, config_json)。name 兜底防空。"""
        executor = await resolve_member(login_user, body.executor)
        verifier = await resolve_member(login_user, body.verifier)
        executor["name"] = executor.get("name") or "executor"
        verifier["name"] = verifier.get("name") or "verifier"
        config = {"executor": executor, "verifier": verifier,
                  "max_iters": 10, "max_retries": 3}
        return executor, verifier, config

    @staticmethod
    async def clone_sources(login_user: dict) -> list[dict]:
        """克隆来源候选：当前用户可见的单智能体（执行者/检查者下拉）。"""
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
        row = await OrchGoalService.get_row(config_id)
        if not row:
            raise ValueError("目标不存在")
        await ensure_any(login_user, RESOURCE_CODE, config_id, [ACTION_USE, ACTION_EDIT],
                         owner_id=row.created_by)

    @staticmethod
    async def runtime_config(config_id: int) -> Optional[dict]:
        """worker 运行时拓扑（不鉴权）：GoalPipeline 构建器所需快照。"""
        row = await OrchGoalService.get_row(config_id)
        if not row:
            return None
        cfg = row.config_json or {}
        return {
            "mode": "goal",
            "config_id": row.id,
            "executor": cfg.get("executor") or {},
            "verifier": cfg.get("verifier") or {},
            "max_iters": int(cfg.get("max_iters") or 10),
            "max_retries": int(cfg.get("max_retries") or 3),
        }
