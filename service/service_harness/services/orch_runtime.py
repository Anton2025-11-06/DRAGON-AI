# -*- coding: utf-8 -*-
"""智能体编排运行时统一分派（会话投递前的准入 + worker 配置解析）。

四模式共享一个入口，按 mode 落到各自 service：
- ensure_runnable：验证台/发消息投递前卡「能不能跑起来」（single=use/edit，
  team/goal/sop 各自 service 同名判定，统一 ensure_any(use, edit)）；
- runtime_config：worker 侧按 mode 解析出运行所需（模型 id / 挂载 / 提示词 /
  流水线拓扑），不含鉴权（准入已在 ensure_runnable 卡过）。

Task 1 只接 single；team/goal/sop 分支在 Task 2/3/4 补齐（届时把 NotImplementedError
替换为对应 service 调用）。缺省 mode=single、config_id=0 即深度探索，不走这里。

本模块另管两件深度探索页与编排交叉的事（同一份 mode 口径只在这维护）：
- usable_agents：「+智能体」弹框候选（四模式一起给，use 口径下推可见集）；
- merge_extra_mounts：页面本轮额外勾选的四类能力该并给谁（按 mode 定角色）。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_USE, build_visible_cond,
)
from service.service_harness.models.orch_agent import (
    OrchAgent, OrchGoal, OrchSop, OrchTeam,
)
from service.service_harness.services.orch_agent_service import OrchAgentService
from service.service_harness.services.orch_goal_service import OrchGoalService
from service.service_harness.services.orch_sop_service import OrchSopService
from service.service_harness.services.orch_team_service import OrchTeamService

# 挂载快照里四类能力 id 列表的键（与 MountsReq / tb_orch_agent.mounts 同名）
MOUNT_KEYS = ("skillIds", "toolIds", "mcpIds", "kbIds")

# 四模式→(实体, ACL 资源码)：选择器列表与合并口径共用这一张表
MODE_SPECS = {
    "single": (OrchAgent, "orch_agent"),
    "team": (OrchTeam, "orch_team"),
    "goal": (OrchGoal, "orch_goal"),
    "sop": (OrchSop, "orch_sop"),
}
# 弹框分组展示顺序（与四类智能体页面同序）
MODE_ORDER = ("single", "team", "goal", "sop")


async def ensure_runnable(login_user: dict, mode: str, config_id: int) -> None:
    """投递前准入：当前用户对这条编排配置是否「能跑起来」（use 或 edit）。"""
    if mode == "single":
        await OrchAgentService.ensure_runnable(login_user, config_id)
        return
    if mode == "team":
        await OrchTeamService.ensure_runnable(login_user, config_id)
        return
    if mode == "goal":
        await OrchGoalService.ensure_runnable(login_user, config_id)
        return
    if mode == "sop":
        await OrchSopService.ensure_runnable(login_user, config_id)
        return
    raise ValueError(f"编排模式 {mode} 尚未接入运行时")


async def runtime_config(mode: str, config_id: int) -> Optional[dict]:
    """worker 运行时解析（不做鉴权）：返回参数化 build_agent / 流水线所需事实。

    single 返回 {mounts, system_prompt, agent_name, status}；
    team/goal/sop 在各自 Task 返回流水线拓扑（成员/主/执行者/检查者/步骤）。
    """
    if mode == "single":
        return await OrchAgentService.runtime_config(config_id)
    if mode == "team":
        return await OrchTeamService.runtime_config(config_id)
    if mode == "goal":
        return await OrchGoalService.runtime_config(config_id)
    if mode == "sop":
        return await OrchSopService.runtime_config(config_id)
    raise ValueError(f"编排模式 {mode} 尚未接入运行时")


# ==================== 深度探索页「+智能体」候选 ====================
async def usable_agents(login_user: dict) -> list[dict]:
    """四类智能体中当前用户「自己创建的或有使用权限的」（use 口径下推）。

    平铺返回 {mode/id/name/purpose}，前端按 mode 分组展示；只给选择器必需的
    公开字段，不外泄提示词/挂载内容（真跑之前 /chat 还会再卡一次 ensure_runnable）。
    只过 is_deleted（与四个列表页同口径：启用/停用已从页面上拿掉）。
    """
    out: list[dict] = []
    async with mysql_client.get_session() as session:
        for mode in MODE_ORDER:
            model, code = MODE_SPECS[mode]
            conds = [model.is_deleted == 0]
            visible = build_visible_cond(login_user, code, model.id, model.created_by,
                                         action=ACTION_USE)
            if visible is not None:
                conds.append(visible)
            rows = (await session.execute(
                select(model).where(*conds).order_by(model.id.desc())
            )).scalars().all()
            out.extend({"mode": mode, "id": r.id, "name": r.name, "purpose": r.purpose}
                       for r in rows)
    return out


# ==================== 页面额外能力 → 本轮执行角色 ====================
def merge_mounts(base: Optional[dict], extra: Optional[dict]) -> dict:
    """两份挂载快照取并集：只并四类能力 id，modelId 一律以 base（角色自带）为准。"""
    base = base or {}
    extra = extra or {}
    out: dict = {}
    if base.get("modelId"):
        out["modelId"] = base["modelId"]
    for key in MOUNT_KEYS:
        ids: list = []
        for src in (base, extra):
            for item in src.get(key) or []:
                if item not in ids:
                    ids.append(item)
        out[key] = ids
    return out


def merge_extra_mounts(cfg: dict, mode: str, extra: Optional[dict]) -> None:
    """把深度探索页本轮勾选的四类能力并进「本轮真正执行的那几个角色」（原地改 cfg）。

    口径（需求明确指定）：单智能体本身 / 团队的队长 / 目标的执行者与检查者 /
    SOP 第一个节点的执行者与检查者——其余成员与后续节点不吃页面勾选，它们只用自己
    配好的挂载。
    """
    if not extra or not any(extra.get(key) for key in MOUNT_KEYS):
        return
    for role in _exec_roles(cfg, mode):
        role["mounts"] = merge_mounts(role.get("mounts"), extra)


def apply_model_override(cfg: dict, mode: str, model_id: int) -> None:
    """页面本轮手动选了模型 → 覆盖那几个执行角色自带的模型（与上面同一批角色）。

    没选（model_id=0）就什么都不做，各角色继续用智能体里配好的模型。
    """
    if not model_id:
        return
    for role in _exec_roles(cfg, mode):
        role["mounts"] = {**(role.get("mounts") or {}), "modelId": model_id}


def _exec_roles(cfg: dict, mode: str) -> list[dict]:
    """本轮该接页面增量配置的那几个角色对象（各自带 mounts）。"""
    if mode == "single":
        return [cfg]
    if mode == "team":
        return _role_dicts([cfg.get("leader")])
    if mode == "goal":
        return _role_dicts([cfg.get("executor"), cfg.get("verifier")])
    if mode == "sop":
        steps = cfg.get("steps") or []
        first = steps[0] if steps and isinstance(steps[0], dict) else {}
        return _role_dicts([first.get("executor"), first.get("verifier")])
    raise ValueError(f"编排模式 {mode} 尚未接入运行时")


def _role_dicts(items: list) -> list[dict]:
    """只留真正存在的角色快照：SOP 节点可以没有检查者。"""
    return [item for item in items if isinstance(item, dict)]
