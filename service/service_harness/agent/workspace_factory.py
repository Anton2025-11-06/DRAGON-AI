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

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from agentscope.app.workspace_manager import IsolationPolicy
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace._opensandbox._constants import DEFAULT_IMAGE

from common.common_log.log_init import log
from service.service_harness.agent.custom_opensandbox import (
    CustomOpenSandboxWorkspaceManager,
)
from service.service_harness.agent.harness_agent import AGENT_ID

from service.service_harness.services.harness_settings import opensandbox_options

# workspace_id 前缀：与其它接入方（如有）在同一个 OpenSandbox 平台上互不抢占
WORKSPACE_PREFIX = "harness"


def workspace_id_of(user_id: int) -> str:
    """用户 → 沙箱 workspace_id（唯一约定，恢复/attach 共用）。

    值需满足 OpenSandbox metadata label 规范（字母数字与 ``-_.``，首尾
    字母数字，≤63 字符）——分隔符用 ``-`` 不用 ``:``。
    """
    return f"{WORKSPACE_PREFIX}-{int(user_id)}"


manager = None


def _get_manager():
    """按当前 opensandbox 配置初始化一个 CustomOpenSandboxWorkspaceManager
    """
    opts = opensandbox_options()
    if not opts["domain"]:
        raise ValueError("OpenSandbox Server 地址未配置")
    # 用 Custom 子类而不是父类：父类 _build_and_start 写死了 new OpenSandboxWorkspace，
    # 只有换 manager，携 skip-bootstrap 的 workspace 子类才会真正被实例化
    log.info(opts)
    global manager
    if not manager:
        manager = CustomOpenSandboxWorkspaceManager(
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
    return manager


async def _attach(manager, login_user: dict, session_id, skill_paths) -> OpenSandboxWorkspace:
    """在给定 manager 上取（并 initialize）当前用户的 workspace，按需种入技能。"""
    user_key = str(int(login_user.get("user_id") or 0))
    # PER_USER 隔离下 agent/session 只是 manager 定位入参，不参与 workspace 归属；
    # 无会话上下文的调用方（沙箱文件浏览）session_id 为空，给固定值保证幂等。
    session_key = str(session_id) if session_id not in (None, "") else "0"
    workspace_id = await manager.assign_workspace_id(
        user_id=user_key, agent_id=AGENT_ID, session_id=session_key)
    # get_workspace 的 miss 路径 _build_and_start 内部已 initialize，返回的就是 live workspace
    workspace = await manager.get_workspace(
        user_key, AGENT_ID, session_key, workspace_id)
    # 挂载技能：本地解压目录 → 沙箱 skills/（List_skills 侧自动可见，重复种入无害）
    # add_skill 一次只收「单个技能目录」（内部直接 os.path.join(skill_path, "SKILL.md")，
    # 传 list 会 TypeError: expected str, bytes or os.PathLike object, not list），须逐个种；
    # 单个技能种入失败只跳过并记 warning，不炸整轮对话（同 mount_loader 静默跳过口径）。
    for skill_path in skill_paths or []:
        try:
            await workspace.add_skill(skill_path)
        except Exception as e:  # noqa: BLE001  单技能失败不影响本轮其它能力
            log.warning("harness skill seed skipped: path={} {}", skill_path, e)
    workdir = str(getattr(workspace, "workdir", "") or "")
    log.info("harness workspace ready: {} workdir={}", workspace_id, workdir)
    return workspace


@asynccontextmanager
async def workspace_scope(login_user: dict, session_id=None,
                          skill_paths: list[str] | None = None) -> AsyncGenerator[OpenSandboxWorkspace, Any]:
    """async with 一整个使用范围（对话轮次 / 沙箱文件浏览）：进入起临时 manager，退出自动释放。

    ``async with OpenSandboxWorkspaceManager`` 的 ``__aexit__`` 会 close_all
    （pause 本轮沙箱 + 停 sweeper），所以整轮/整请求跑完（含异常）不用手工 teardown。
    每次各起临时 manager、互不共享，退出只影响本次自己，不会 close 掉别的并发请求。

    manager 实际是 custom_opensandbox.CustomOpenSandboxWorkspaceManager（跳过首建
    引导命令，适配已预装网关 venv 的自建镜像），接口与父类一致。
    """
    manager = _get_manager()
    async with manager:
        yield await _attach(manager, login_user, session_id, skill_paths)
