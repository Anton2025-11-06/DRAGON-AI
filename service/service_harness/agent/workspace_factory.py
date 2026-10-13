# -*- coding: utf-8 -*-
"""沙箱工作区工厂：每用户一个 OpenSandbox 云沙箱。

用户数据隔离的核心机制：
- workspace_id = ``harness-{user_id}``（每用户独立沙箱实例）。分隔符用
  ``-``：workspace_id 会作为沙箱 metadata label 值上报，OpenSandbox 服务端
  按 K8s label 规范校验（≤63 字符、首尾字母数字、仅字母数字与 ``-_.``，
  冒号会被拒 INVALID_METADATA_LABEL）；
- teardown 不能调 pause（OpenSandboxWorkspace 没有该方法）：沙箱由服务端
  timeout_seconds 自然暂停，文件系统保留，下一轮 initialize 经平台 metadata
  查 workspace_id reattach+resume——用户文件跨会话不丢；
- 同一用户同一 workspace_id 不会建出第二个沙箱（attach 是幂等的）。

配置来源 harness_settings.opensandbox_options()（Nacos service_harness 段的
opensandbox 小节）；domain 未配置直接报「未部署」，不硬造默认地址。

本模块只被 arq worker import（同 model_factory 的口径）。
"""
from __future__ import annotations

from agentscope.app.workspace_manager import IsolationPolicy
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace._opensandbox._constants import DEFAULT_IMAGE

from common.common_log.log_init import log
from service.service_harness.agent.harness_agent import AGENT_ID

from service.service_harness.services.harness_settings import opensandbox_options

# workspace_id 前缀：与其它接入方（如有）在同一个 OpenSandbox 平台上互不抢占
WORKSPACE_PREFIX = "harness"
workspace_manager=None

def workspace_id_of(user_id: int) -> str:
    """用户 → 沙箱 workspace_id（唯一约定，恢复/attach 共用）。

    值需满足 OpenSandbox metadata label 规范（字母数字与 ``-_.``，首尾
    字母数字，≤63 字符）——分隔符用 ``-`` 不用 ``:``。
    """
    return f"{WORKSPACE_PREFIX}-{int(user_id)}"


async def build_workspace(login_user: dict, session_id=None,
                          skill_paths: list[str] | None = None) -> OpenSandboxWorkspace:
    """建/attach 当前用户的云沙箱工作区（走 OpenSandboxWorkspaceManager）。

    PER_USER 隔离：同一用户各会话、各接入方（对话轮次、沙箱文件浏览）都指向
    同一 workspace，文件系统跨会话保留；manager 进程内单例复用，自带 attach
    幂等（reattach+resume），业务侧不另建缓存、也不手工 initialize。

    :param login_user: JWT 载荷 dict，取 user_id 作为隔离主体
    :param session_id: 会话 id，manager 定位入参之一；PER_USER 下不影响 workspace
        归属，无会话上下文的接入方（沙箱文件浏览）可不传，内部按固定值兜底。
    :param skill_paths: 挂载技能解压后的本地目录，构建后 add_skill 种入 skills/。
    :raises ValueError: opensandbox 段未配置（domain 缺失）
    """
    global workspace_manager
    opts = opensandbox_options()
    if not opts["domain"]:
        raise ValueError("OpenSandbox Server 地址未配置")

    from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
    # manager 进程内单例：配置固定，首次构建后长期复用（PER_USER 按用户定位 workspace）
    if workspace_manager is None:
        workspace_manager = OpenSandboxWorkspaceManager(
            isolation=IsolationPolicy.PER_USER,
            image=opts["image"] if opts["image"] else DEFAULT_IMAGE,
            api_key=opts["api_key"],
            domain=opts["domain"],
            protocol=opts["protocol"],
            request_timeout_seconds=opts["request_timeout_seconds"],
            timeout_seconds=opts["timeout_seconds"],
            resource=opts["resource"],
            env=opts["env"],
        )

    user_key = str(int(login_user.get("user_id") or 0))
    # PER_USER 隔离下 agent/session 只是 manager 定位入参，不参与 workspace 归属；
    # 无会话上下文的调用方（沙箱文件浏览）session_id 为空，给固定值保证幂等。
    session_key = str(session_id) if session_id not in (None, "") else "0"
    workspace_id = await workspace_manager.assign_workspace_id(
        user_id=user_key, agent_id=AGENT_ID, session_id=session_key)
    workspace = await workspace_manager.get_workspace(
        user_key, AGENT_ID, session_key, workspace_id)

    # 挂载技能：本地解压目录 → 沙箱 skills/（List_skills 侧自动可见，重复种入无害）
    if skill_paths:
        await workspace.add_skill(skill_paths)

    workdir = str(getattr(workspace, "workdir", "") or "")
    log.info("harness workspace ready: {} workdir={}", workspace_id, workdir)
    return workspace
