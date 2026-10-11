# -*- coding: utf-8 -*-
"""沙箱工作区工厂：每用户一个 OpenSandbox 云沙箱。

用户数据隔离的核心机制：
- workspace_id = ``harness-{user_id}``（每用户独立沙箱实例）。分隔符用
  ``-``：workspace_id 会作为沙箱 metadata label 值上报，OpenSandbox 服务端
  按 K8s label 规范校验（≤63 字符、首尾字母数字、仅字母数字与 ``-_.``，
  冒号会被拒 INVALID_METADATA_LABEL）；
- teardown 用 pause 而不是删：文件系统保留，下一轮 initialize 经平台 metadata
  查 workspace_id reattach + resume——用户文件跨会话不丢；
- 同一用户同一 workspace_id 不会建出第二个沙箱（attach 是幂等的）。

配置来源 harness_settings.opensandbox_options()（Nacos service_harness 段的
opensandbox 小节）；domain 未配置直接报「未部署」，不硬造默认地址。

本模块只被 arq worker import（同 model_factory 的口径）。
"""
from __future__ import annotations

from agentscope.workspace import OpenSandboxWorkspace

from common.common_log.log_init import log

from service.service_harness.services.harness_settings import opensandbox_options

# workspace_id 前缀：与其它接入方（如有）在同一个 OpenSandbox 平台上互不抢占
WORKSPACE_PREFIX = "harness"


def workspace_id_of(user_id: int) -> str:
    """用户 → 沙箱 workspace_id（唯一约定，恢复/attach 共用）。

    值需满足 OpenSandbox metadata label 规范（字母数字与 ``-_.``，首尾
    字母数字，≤63 字符）——分隔符用 ``-`` 不用 ``:``。
    """
    return f"{WORKSPACE_PREFIX}-{int(user_id)}"


async def build_workspace(login_user: dict, skill_paths: list[str] | None = None) -> OpenSandboxWorkspace:
    """建/attach 当前用户的云沙箱并完成初始化，返回可用的工作区。

    :param skill_paths: 挂载技能解压后的本地目录（OpenSandbox 在 initialize
        时种入沙箱 skills/；reattach 场景沙箱已有个体，重复传入无害）。
    :raises ValueError: opensandbox 段未配置（domain 缺失）
    """
    opts = opensandbox_options()
    if not opts["domain"]:
        raise ValueError("OpenSandbox 未配置（Nacos service_harness 段缺 opensandbox.domain）")
    user_id = int(login_user.get("user_id") or 0)
    # image 未配置时【不传该参数】：显式传 None 不会落到构造签名默认值，
    # agentscope 的 _create_sandbox 会把 None 原样交给 Sandbox.create，
    # 服务端校验 image/snapshot_id 恰好其一非空 → INVALID_ARGUMENT。
    # 不传 = agentscope 默认镜像 python:3.11-slim（bootstrap 按它设计）
    image_kwargs = {"image": opts["image"]} if opts["image"] else {}
    workspace = OpenSandboxWorkspace(
        workspace_id=workspace_id_of(user_id),
        domain=opts["domain"],
        api_key=opts["api_key"],
        protocol=opts["protocol"],
        request_timeout_seconds=opts["request_timeout_seconds"],
        timeout_seconds=opts["timeout_seconds"],
        resource=opts["resource"],
        env=opts["env"],
        # 挂载技能：本地解压目录 → 沙箱 skills/（List_skills 侧自动可见）
        skill_paths=skill_paths or None,
        **image_kwargs,
    )
    # initialize 幂等：已存在的 workspace_id 走 reattach+resume（文件系统保留）
    await workspace.initialize()
    log.info("harness workspace ready: {} workdir={}",
             workspace_id_of(user_id), workspace.workdir)
    return workspace
