# -*- coding: utf-8 -*-
"""智能体编排运行时统一分派（会话投递前的准入 + worker 配置解析）。

四模式共享一个入口，按 mode 落到各自 service：
- ensure_runnable：验证台/发消息投递前卡「能不能跑起来」（single=use/edit，
  team/goal/sop 各自 service 同名判定，统一 ensure_any(use, edit)）；
- runtime_config：worker 侧按 mode 解析出运行所需（模型 id / 挂载 / 提示词 /
  流水线拓扑），不含鉴权（准入已在 ensure_runnable 卡过）。

Task 1 只接 single；team/goal/sop 分支在 Task 2/3/4 补齐（届时把 NotImplementedError
替换为对应 service 调用）。缺省 mode=single、config_id=0 即深度探索，不走这里。
"""
from __future__ import annotations

from typing import Optional

from service.service_harness.services.orch_agent_service import OrchAgentService
from service.service_harness.services.orch_goal_service import OrchGoalService
from service.service_harness.services.orch_sop_service import OrchSopService
from service.service_harness.services.orch_team_service import OrchTeamService


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
