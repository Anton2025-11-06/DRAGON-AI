# -*- coding: utf-8 -*-
"""沙箱工作区工厂：每用户一个 OpenSandbox 云沙箱。

用户数据隔离的核心机制：
- workspace_id 由 manager 按 PER_USER 隔离策略分配（每用户独立沙箱实例）。
  历史上曾用 ``harness-{user_id}`` 手工约定：workspace_id 会作为沙箱
  metadata label 值上报，OpenSandbox 服务端按 K8s label 规范校验（≤63 字符、
  首尾字母数字、仅字母数字与 ``-_.``，冒号会被拒 INVALID_METADATA_LABEL）；
- manager 是进程级单例（见下 :func:`start_manager`）：API(FastAPI) 进程与 arq
  worker 进程各自在自己进程的启动钩子里建一份、退出钩子里释放；同进程多轮
  对话 / 多次文件浏览复用这一份，workspace 按 workspace_id 进 TTL 缓存跨轮
  命中同一只沙箱，到期由 sweeper 自动逐出——不再每轮/每请求临时起 manager；
- 同一用户同一 workspace_id 不会建出第二个沙箱（attach 是幂等的）。

配置来源 harness_settings.opensandbox_options()（Nacos service_harness 段的
opensandbox 小节）；domain 未配置直接报「未部署」，不硬造默认地址。

本模块被 arq worker 与 service_harness API 进程（沙箱文件浏览）共同 import。
"""
from __future__ import annotations

import hashlib
import os

from agentscope.app.workspace_manager import IsolationPolicy, DockerWorkspaceManager, PrewarmConfig
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace._opensandbox._constants import DEFAULT_IMAGE
from agentscope.workspace._sandboxed_base import SandboxedWorkspaceBase

from common.common_log.log_init import log
from service.service_harness.agent.custom_opensandbox import (
    CustomOpenSandboxWorkspaceManager,
)
from service.service_harness.agent.harness_agent import AGENT_ID

from service.service_harness.services.harness_settings import opensandbox_options

# workspace_id 前缀：与其它接入方（如有）在同一个 OpenSandbox 平台上互不抢占
WORKSPACE_PREFIX = "harness"


def workspace_id_of(user_id: int) -> str:
    """用户 → 沙箱 workspace_id（兜底约定，manager 分配不到时回落）。

    值需满足 OpenSandbox metadata label 规范（字母数字与 ``-_.``，首尾
    字母数字，≤63 字符）——分隔符用 ``-`` 不用 ``:``。
    """
    return f"{WORKSPACE_PREFIX}-{int(user_id)}"


def build_manager() -> CustomOpenSandboxWorkspaceManager:
    """按进程内已装载的 opensandbox 配置构造 manager（只构造，不进入生命周期）。

    须在 harness_settings.bootstrap 装好 opensandbox 段之后调用，否则
    opensandbox_options() 读到空、domain 缺失直接报「未配置」。
    """
    opts = opensandbox_options()
    if not opts["domain"]:
        raise ValueError("OpenSandbox Server 地址未配置")
    return CustomOpenSandboxWorkspaceManager(
        isolation=IsolationPolicy.PER_USER,
        image=opts["image"] or DEFAULT_IMAGE,
        api_key=opts["api_key"],
        domain=opts["domain"],
        # protocol=opts["protocol"],
        # request_timeout_seconds=opts["request_timeout_seconds"],
        # 沙箱存活时间，时间到自动销毁，需要手动触发renew-expire方法续期
        timeout_seconds=3600,
        # memory 必须带 K8s 风格单位（Mi/Gi）：裸数字会被当字节解析，
        resource={"cpu": "1", "memory": "1Gi"},
        # env=opts["env"],
    )


def _dir_signature(skill_path: str) -> str:
    """技能目录的内容签名：遍历各文件相对路径 + 字节，blake2b 汇总。

    内容不变签名不变；文件增删改名或字节改动都会变。比 mtime 稳（重解压
    会刷 mtime 但内容可能没变），本地小目录读一遍代价远低于一次沙箱 round-trip。
    """
    root = os.path.abspath(skill_path)
    digest = hashlib.blake2b(digest_size=16)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            file_path = os.path.join(dirpath, name)
            rel = os.path.relpath(file_path, root).replace("\\", "/")
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            try:
                with open(file_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        digest.update(chunk)
            except OSError:
                # 不可读文件只把标记计入，保证同一目录签名稳定
                digest.update(b"<unreadable>")
            digest.update(b"\x01")
    return digest.hexdigest()


async def _sandbox_is_alive(workspace) -> bool:
    """探查 workspace 当前绑定的沙箱在 OpenSandbox 服务端是否真的 running。

    manager 缓存命中时不会做远端校验，句柄可能对应一只已被服务端按**最大存活
    时间**销毁/暂停的沙箱。这里用沙箱句柄向控制面查一次 lifecycle 状态：仅
    RUNNING 判为“活着可直接复用”；其它状态（PAUSED/TERMINATED/FAILED/…）或查询
    异常（沙箱已不存在）都判为失效，交由调用方逐出重建。state 为普通字符串
    （框架自身 ``_attach_existing_sandbox`` 就是用 ``info.status.state.lower()`` 判的）。
    """
    sandbox = getattr(workspace, "_sandbox", None)
    if sandbox is None:
        return False
    try:
        info = await sandbox.get_info()
    except Exception as e:  # noqa: BLE001  沙箱已被服务端回收 / 查不到
        log.warning(
            "harness workspace sandbox probe failed: workspace_id={} {}",
            getattr(workspace, "workspace_id", ""), e)
        return False
    status = getattr(info, "status", None)
    return str(getattr(status, "state", "") or "").lower() == "running"


async def _attach(manager, login_user: dict, session_id, skill_paths) -> SandboxedWorkspaceBase:
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
    # OpenSandbox 服务端有沙箱最大存活时间，到期会把沙箱销毁/暂停，而 manager 的
    # TTL 缓存只按本地时间记账、不会主动发现——此时缓存里的 workspace 句柄指向
    # 一只已不在 running 的沙箱，后续 backend 调用必挂。取到 workspace 后向控制面
    # 探一次真实状态：非 running 就 close 逐出缓存再 get 一次（第二趟是 cache miss，
    # 走 _build_and_start→initialize→_find_existing_sandbox：同 id 沙箱若只是暂停就
    # resume、若已被彻底销毁则新建一只），保证返回的句柄确实可用。只重试一次。
    if not await _sandbox_is_alive(workspace):
        log.warning(
            "harness workspace {} 缓存句柄探活失败（沙箱非 running），逐出缓存后重建",
            workspace_id)
        await manager.close(workspace_id)
        workspace = await manager.get_workspace(
            user_key, AGENT_ID, session_key, workspace_id)
    # 挂载技能：本地解压目录 → 沙箱 ``skills/<agent_id>/`` 分区。
    # add_skill 不带 agent_id 会落进 skills/default，而 build_toolkit 的
    # list_skills(agent_id=AGENT_ID) 读的是 skills/harness_agent/——两套分区，
    # 种进去的技能其实一直没生效；这里显式传 agent_id 对齐。
    # add_skill 一次只收「单个技能目录」（内部直接 os.path.join(skill_path,
    # "SKILL.md")，传 list 会 TypeError），须逐个种；sandboxed add_skill 遇同名
    # 目录直接 raise（不像 LocalWorkspace 按 hash 跳过），故刷新前先删旧副本。
    #
    # 去重复挂：按本地技能目录内容签名（见 _dir_signature）记账在 workspace 对象上
    # ——进程级 manager 单例让 workspace 对象跨轮存活，这份签名缓存随之存活。签名
    # 未变则整段跳过：既省掉每轮 delete+add_skill 的沙箱 round-trip（旧写法每轮无
    # 条件重拷），又杜绝「删了旧副本、新副本又没拷进去」导致的本轮技能丢失。签名
    # 只在成功种入后才记账，失败下轮自动重试；内容变过（zip 被替换）签名随之变，
    # 仍走删+加刷新——冷启动/被逐出后缓存为空，退回每轮重种（与旧行为一致，安全）。
    # 单个技能种入失败只跳过并记 warning，不炸整轮对话（同 mount_loader 口径）。
    backend = workspace.get_backend()
    skills_root = workspace._skills_dir  # 框架属性：{workdir}/skills
    partition = backend.join_path(skills_root, AGENT_ID)
    seeded: dict[str, str] = getattr(workspace, "_harness_seeded_skills", None)
    if seeded is None:
        seeded = {}
        workspace._harness_seeded_skills = seeded
    for skill_path in skill_paths or []:
        try:
            dir_name = os.path.basename(os.path.abspath(skill_path))
            signature = _dir_signature(skill_path)
            if seeded.get(dir_name) == signature:
                # 内容未变且本 workspace 上一轮已成功种入 → 不删不拷，直接跳过
                continue
            stale = backend.join_path(partition, dir_name)
            if await backend.file_exists(stale):
                await backend.delete_path(stale)
            await workspace.add_skill(skill_path, agent_id=AGENT_ID)
            seeded[dir_name] = signature  # 仅成功种入后记账，失败下轮自动重试
        except Exception as e:  # noqa: BLE001  单技能失败不影响本轮其它能力
            log.warning("harness skill seed skipped: path={} {}", skill_path, e)
    workdir = str(getattr(workspace, "workdir", "") or "")
    log.info("harness workspace ready: {} workdir={}", workspace_id, workdir)
    return workspace


# 进程级 manager 单例：API(FastAPI) 进程与 arq worker 进程各在自己进程的启动
# 钩子里建一次、进入生命周期（__aenter__ 起 sweeper/预热），退出钩子里 __aexit__
# 统一 close_all。同进程内多轮对话 / 多次文件浏览复用这一份，workspace 按
# workspace_id 进 TTL 缓存，跨轮命中同一只沙箱、到期由 sweeper 自动逐出——
# 不再每轮/每请求临时起 manager。
_MANAGER: CustomOpenSandboxWorkspaceManager | None = None


async def start_manager(
    manager: CustomOpenSandboxWorkspaceManager | None = None,
) -> CustomOpenSandboxWorkspaceManager:
    """构造（或采用传入的）并进入进程级 manager 单例，幂等。

    :param manager: 已由调用方构造好的 manager（API 侧从 Nacos yml 直接建好再
        传进来）；留空则按已装载的 opensandbox 配置自行 :func:`build_manager`。
    """
    global _MANAGER
    if _MANAGER is not None:
        return _MANAGER
    _MANAGER = manager if manager is not None else build_manager()
    # await _MANAGER.__aenter__()
    return _MANAGER


async def stop_manager() -> None:
    """退出进程级 manager 单例：__aexit__ 会 close_all 缓存沙箱并停 sweeper。"""
    global _MANAGER
    if _MANAGER is None:
        return
    manager, _MANAGER = _MANAGER, None
    await manager.__aexit__(None, None, None)


def get_manager() -> CustomOpenSandboxWorkspaceManager:
    """取进程级 manager 单例；取不到说明本进程启动没调 :func:`start_manager`。"""
    if _MANAGER is None:
        raise RuntimeError(
            "workspace manager 未初始化：进程启动须先调 workspace_factory.start_manager()")
    return _MANAGER


async def attach_workspace(login_user: dict, session_id=None,
                           skill_paths: list[str] | None = None
                           ) -> SandboxedWorkspaceBase:
    """取（并 initialize）当前用户的 workspace——复用进程级 manager 单例。

    不做 per-turn/per-request 上下文管理：沙箱轮末不关闭，进 manager 的 TTL 缓存
    跨轮/跨请求复用，到期由 sweeper 逐出，进程退出由 :func:`stop_manager` 统一
    close_all。manager 实际是 custom_opensandbox.CustomOpenSandboxWorkspaceManager
    （跳过首建引导命令，适配已预装网关 venv 的自建镜像），接口与父类一致。
    """
    return await _attach(get_manager(), login_user, session_id, skill_paths)
