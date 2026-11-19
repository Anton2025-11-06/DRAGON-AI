# -*- coding: utf-8 -*-
"""单智能体编排（tb_orch_agent）：CRUD + 搜索 + 查看 + 授权口径 + 运行时配置。

权限两层（口径见 common_permission/resource_guard 顶部与计划 §1）：
- RBAC 只管「全局入口 + 页面准入」：创建/列表/授权入口的 @has_permission 在 router；
- 行级按钮（查看/使用/编辑/删除/授权 share）一律由 ACL 行内 actions 驱动：
  * 列表可见 = build_visible_cond(view)（自己创建 ∪ 数据范围内 ∪ 显式授 view）；
  * 逐行按钮 = decorate_actions 下发 actions，前端据此显隐；
  * 单点判定：detail→ensure_action(view)、update→ensure_action(edit)、
    delete→ensure_action_strict(delete)（严格：同部门不自动放开）；
  * 验证 = 等价 edit：后端执行入口走 ensure_any(use, edit)（能跑起来才能验证）。

克隆语义（计划 §6）：团队/目标/SOP 从本表克隆成员时，逐成员对 mounts 里的
skill/tool/mcp/kb 校验当前用户 use 权限，无权项静默过滤——复用 mount_loader
同款的「use 口径可见集合 ∩ 声明的 id」，与 picker「选得中即运行可用」一致。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, or_, select

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_DELETE, ACTION_EDIT, ACTION_USE, ACTION_VIEW, build_visible_cond,
    decorate_actions, ensure_action, ensure_action_strict, ensure_any,
)
from common.common_utils.creator_util import attach_creator
from service.service_harness.models.orch_agent import OrchAgent
from service.service_harness.services.options_service import (
    OPTIONS_KINDS, HarnessOptionsService,
)

# 本资源在 ACL 引擎里的资源编码（与 RESOURCE_SPECS / GRANT_PERMISSIONS 的键一致）
RESOURCE_CODE = "orch_agent"
# mounts 里能力 id 列表的键 → options 的 kind（键名与运行时/MountsReq 一致）
MOUNT_KIND_MAP = {"skillIds": "skill", "toolIds": "tool", "mcpIds": "mcp", "kbIds": "kb"}


def _fmt_time(value) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class OrchAgentService:
    """单智能体编排服务（CRUD + 搜索 + 查看 + 授权 + 运行时配置）。"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(login_user: dict, page: int = 1, page_size: int = 10,
                   keyword: str = None, status: int = None) -> dict:
        """分页 + 搜索（名称/用途）；行可见由 ACL 下推，行内按钮由 decorate_actions 下发。"""
        async with mysql_client.get_session() as session:
            conds = [OrchAgent.is_deleted == 0]
            if keyword:
                like = f"%{keyword}%"
                conds.append(or_(OrchAgent.name.like(like), OrchAgent.purpose.like(like)))
            if status is not None:
                conds.append(OrchAgent.status == status)
            visible = build_visible_cond(login_user, RESOURCE_CODE, OrchAgent.id,
                                         OrchAgent.created_by)
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(OrchAgent).where(*conds))).scalar()
            rows = (await session.execute(
                select(OrchAgent).where(*conds)
                .order_by(OrchAgent.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [OrchAgentService._to_row(r, with_prompt=False) for r in rows]
            await decorate_actions(login_user, RESOURCE_CODE, items, session=session)
            await attach_creator(items, id_key="created_by", session=session)
            return {"total": total, "items": items}

    @staticmethod
    def _to_row(row: OrchAgent, *, with_prompt: bool) -> dict:
        """ORM 行 → 字典（列名只在这一处维护）。列表不带 system_prompt 全文，详情带。"""
        out = {
            "id": row.id, "name": row.name, "purpose": row.purpose,
            "mounts": row.mounts or {}, "status": row.status,
            "created_by": row.created_by, "updated_by": row.updated_by,
            "create_time": _fmt_time(row.create_time),
            "update_time": _fmt_time(row.update_time),
        }
        if with_prompt:
            out["systemPrompt"] = row.system_prompt
        return out

    @staticmethod
    async def get_row(id_: int) -> Optional[OrchAgent]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(OrchAgent).where(OrchAgent.id == id_,
                                        OrchAgent.is_deleted == 0)
            )).scalar_one_or_none()

    @staticmethod
    async def detail(login_user: dict, id_: int) -> Optional[dict]:
        """查看：先确保资源存在，再卡 ACL view；返回完整配置（含 system_prompt）。"""
        row = await OrchAgentService.get_row(id_)
        if not row:
            return None
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_VIEW,
                            owner_id=row.created_by)
        return OrchAgentService._to_row(row, with_prompt=True)

    # ==================== 新增 / 编辑 / 删除 ====================
    @staticmethod
    async def create(login_user: dict, name: str, purpose: Optional[str],
                     system_prompt: Optional[str], mounts: Optional[dict],
                     status: bool = True) -> int:
        """创建：入口权限（RBAC add）在 router 卡；这里落库并记归属人。"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = OrchAgent(name=name, purpose=purpose, system_prompt=system_prompt,
                            mounts=mounts or {}, status=1 if status else 0,
                            created_by=user_id)
            session.add(row)
            await session.flush()
            new_id = row.id
            await session.commit()
            log.info("orch_agent created: id={} name={}", new_id, name)
            return new_id

    @staticmethod
    async def update(login_user: dict, id_: int, *, name: Optional[str] = None,
                     purpose: Optional[str] = None, system_prompt: Optional[str] = None,
                     mounts: Optional[dict] = None, status: Optional[bool] = None) -> bool:
        """编辑：卡 ACL edit（有编辑即有验证）；只改真正传入的字段。"""
        row = await OrchAgentService.get_row(id_)
        if not row:
            raise ValueError("智能体不存在")
        await ensure_action(login_user, RESOURCE_CODE, id_, ACTION_EDIT,
                            owner_id=row.created_by)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchAgent, id_)
            if obj is None or obj.is_deleted:
                raise ValueError("智能体不存在")
            if name is not None:
                obj.name = name
            if purpose is not None:
                obj.purpose = purpose
            if system_prompt is not None:
                obj.system_prompt = system_prompt
            if mounts is not None:
                obj.mounts = mounts
            if status is not None:
                obj.status = 1 if status else 0
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_agent updated: id={}", id_)
            return True

    @staticmethod
    async def delete(login_user: dict, id_: int) -> bool:
        """删除：软删；卡 ACL 严格 delete（同部门不自动放开，仅归属人/ADMIN/显式授 delete）。"""
        row = await OrchAgentService.get_row(id_)
        if not row:
            return False
        await ensure_action_strict(login_user, RESOURCE_CODE, id_, ACTION_DELETE)
        async with mysql_client.get_session() as session:
            obj = await session.get(OrchAgent, id_)
            if obj is None or obj.is_deleted:
                return False
            obj.is_deleted = 1
            obj.updated_by = int(login_user.get("user_id") or 0)
            await session.commit()
            log.info("orch_agent deleted(soft): id={}", id_)
            return True

    # ==================== 可用资源 / 克隆过滤（四模式共用口径） ====================
    @staticmethod
    async def usable_resources(login_user: dict, kind: str) -> list[dict]:
        """按 kind 复用 HarnessOptionsService.options（use 口径，仅公开信息，不泄露密钥）。

        只回 id/name/description：这些是 picker 展示与克隆交集所需的最小公开集，
        永不含连接串/密钥等敏感字段（options 侧本就已裁）。
        """
        if kind not in OPTIONS_KINDS:
            raise ValueError(f"未知能力类型: {kind}")
        return await HarnessOptionsService.options(login_user, kind)

    @staticmethod
    async def filter_mounts_for_user(login_user: dict, mounts: Optional[dict]) -> dict:
        """克隆过滤（计划 §6）：把 mounts 里当前用户无 use 权的能力 id 静默剔除。

        复用 options 的 use 口径可见集合取交集——与 picker「选得中即运行可用」、
        mount_loader._intersect「越权静默丢弃」同一口径，三处不会出现判定分叉。
        modelId 不在此过滤（模型可用性由可选清单接口收口）。
        """
        mounts = mounts or {}
        out: dict[str, Any] = {}
        model_id = mounts.get("modelId")
        if model_id:
            out["modelId"] = model_id
        for key, kind in MOUNT_KIND_MAP.items():
            wanted = mounts.get(key) or []
            if not wanted:
                out[key] = []
                continue
            allowed = {int(o["id"]) for o in await HarnessOptionsService.options(login_user, kind)
                       if o.get("id") is not None}
            out[key] = [int(v) for v in wanted if str(v).lstrip("-").isdigit()
                        and int(v) in allowed]
        return out

    # ==================== 运行时 / 验证台入口 ====================
    @staticmethod
    async def ensure_runnable(login_user: dict, config_id: int) -> None:
        """验证台执行入口（single）：卡「任一动作」= use 或 edit（能跑起来才能验证，计划 §1）。

        team/goal/sop 的入口由各自 service 提供同名方法，统一经 orch_runtime 按 mode 分派。
        """
        row = await OrchAgentService.get_row(config_id)
        if not row:
            raise ValueError("智能体不存在")
        await ensure_any(login_user, RESOURCE_CODE, config_id, [ACTION_USE, ACTION_EDIT],
                         owner_id=row.created_by)

    @staticmethod
    async def runtime_config(config_id: int) -> Optional[dict]:
        """worker 运行时读取（不做鉴权，鉴权已在投递前的 ensure_runnable 卡过）。

        返回参数化 build_agent 需要的：mounts（含 modelId）、system_prompt、agent_name。
        软删/不存在返回 None（worker 据此报「配置不存在」）。
        """
        row = await OrchAgentService.get_row(config_id)
        if not row:
            return None
        return {
            "mounts": row.mounts or {},
            "system_prompt": row.system_prompt,
            "agent_name": f"orch_agent_{row.id}",
            "status": row.status,
        }
