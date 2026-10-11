# -*- coding: utf-8 -*-
"""深度探索 harness 运行参数归一（Nacos ``service_harness`` 的 opensandbox/agentscope 段）。

为什么这一层单独存在（与 rag_settings 同一套理由）
------------------------------------------------
1. **配置只有一个入口**：API 进程（on_ready）与 harness worker（bootstrap）都
   从这里拿同一份缺省值，否则两侧的沙箱超时/存储 db 会出现「同一个会话两边
   两个口径」。
2. **量级参数全部夹区间**：页面/运维能填就能填错，越界回落默认而不是把异常
   抛给一轮对话。
3. **缺省回落必须显式**：agentscope storage 段没配时回落平台 redis 段（db 换
   1，与业务 key 隔离）；opensandbox.domain 没配不硬造默认地址——没部署就是
   没部署，报「未配置」比报「连不上陌生地址」好排查。

进程内装载方式
--------------
``bootstrap(config)`` 在 API 服务的 on_ready 钩子（收到本服务 yml）与 harness
worker 的 bootstrap（读 SERVICE_HARNESS 段后显式传入）各调一次；没装载时全部
回落代码默认，单元测试与脚本里没有 Nacos 不会炸。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from common.common_log.log_init import log

# Nacos 里的段落名（与部署侧 init_nacos.yaml 约定一致）
SECTION_OPENSANDBOX = "opensandbox"
SECTION_AGENTSCOPE = "agentscope"
SECTION_SKILLS = "skills"

# agentscope RedisStorage 缺省回落用的 db：平台业务库是 0，AgentState 独立用 1，
# 避免与业务 key（rate limit / online user 等）互相 SCAN 到对方的键形态
STORAGE_DB_FALLBACK = 1

# 已装载的配置（进程级；空字典 = 还没 bootstrap 过）
_SECTION: dict[str, Any] = {}
# bootstrap 时顺手缓存的平台 redis 段（agentscope 段缺省回落的取数源）
_REDIS_FALLBACK: dict[str, Any] = {}


# ==================== 装载 ====================

async def bootstrap(config: Optional[dict] = None) -> dict:
    """装载运行参数（幂等，可重复调用刷新）。

    :param config: 已读好的整份 yml（API 侧 on_ready 收到的那份 / worker 侧读
        完 SERVICE_HARNESS 的那份）；留空则自行向 Nacos 取。
    :return: 已装载的段落快照（给启动日志与自检用）
    """
    global _SECTION, _REDIS_FALLBACK
    if config is None:
        config = await _pull_from_nacos()
    _SECTION = {
        SECTION_OPENSANDBOX: config.get(SECTION_OPENSANDBOX) or {},
        SECTION_AGENTSCOPE: config.get(SECTION_AGENTSCOPE) or {},
        SECTION_SKILLS: config.get(SECTION_SKILLS) or {},
    }
    _REDIS_FALLBACK = dict(config.get("redis") or {})
    log.info("harness settings loaded: opensandbox_domain={} storage_db={}",
             opensandbox_options()["domain"] or "(unset)", storage_options()["db"])
    return _SECTION


async def _pull_from_nacos() -> dict:
    """没有显式传入时回落配置中心（读失败只告警，让调用方带着默认值继续）。"""
    try:
        from common.common_constants.constant import SERVICE_HARNESS
        from common.common_nacos.nacos_client import nacos_client

        return await nacos_client.get_config_content(SERVICE_HARNESS) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("harness settings 读取 Nacos 失败，本轮使用代码默认值: {}", e)
        return {}


def is_loaded() -> bool:
    """opensandbox/agentscope 两段是否装载过（自检用）。"""
    return bool(_SECTION)


# ==================== 归一取值 ====================

def _as_int(value: Any, low: int, high: int, default: int) -> int:
    """取整数并夹到 [low, high]；非数字/越界回落默认（填错不该让一轮对话失败）。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return num if low <= num <= high else default


def opensandbox_options() -> dict:
    """OpenSandbox 云沙箱连接参数（原样交给 workspace_factory，本模块不做连接）。

    domain 不给默认值：见模块 docstring 第 3 条。
    """
    cfg = dict(_SECTION.get(SECTION_OPENSANDBOX) or {})
    protocol = str(cfg.get("protocol") or "http").strip().lower()
    resource = cfg.get("resource")
    env = cfg.get("env")
    return {
        "domain": str(cfg.get("domain") or "").strip(),
        "api_key": str(cfg.get("api_key") or "").strip(),
        "protocol": "https" if protocol == "https" else "http",
        # 空串传 None：让 OpenSandboxWorkspace 用它自带的默认镜像
        "image": str(cfg.get("image") or "").strip() or None,
        # SDK 单次 HTTP 请求超时：默认 600 对齐 agentscope 的
        # BOOTSTRAP_COMMAND_TIMEOUT（首建沙箱的单条引导命令能合法流几分钟，
        # 比它短会在首次 bootstrap 时把连接掐死）
        "request_timeout_seconds": _as_int(cfg.get("request_timeout_seconds"),
                                           60, 3600, 600),
        # 沙箱内单条命令/引导等待上限（首次 bootstrap 分钟级，默认给 1h）
        "timeout_seconds": _as_int(cfg.get("timeout_seconds"), 30, 86400, 3600),
        "resource": dict(resource) if isinstance(resource, dict) else None,
        "env": {str(k): str(v) for k, v in env.items()}
        if isinstance(env, dict) else None,
    }


def skill_cache_dir() -> str:
    """挂载技能 zip 的解压缓存根目录（worker 侧本地磁盘）。

    Nacos skills 段的 dir；未配置回落系统临时目录下的 harness_skills——
    技能种入是「拷贝进沙箱」的前置步骤，本地缓存丢了重挂一轮就重建，
    不值得为它阻断 worker 启动。
    """
    import tempfile

    cfg = dict(_SECTION.get(SECTION_SKILLS) or {})
    configured = str(cfg.get("dir") or "").strip()
    return configured or str(Path(tempfile.gettempdir()) / "harness_skills")


def storage_options() -> dict:
    """agentscope RedisStorage 的连接参数。

    agentscope 段给了用它的（host/port/db/password 四键）；没给回落平台 redis
    段（host/port/password）+ db=1。API 进程与 worker 进程装载同一份来源
    （都是 SERVICE_HARNESS 那份 yml），回落结果两侧一致，不存在「API 写进
    db1、worker 从 db0 读」的分裂。
    """
    cfg = dict(_SECTION.get(SECTION_AGENTSCOPE) or {})
    host = str(cfg.get("host") or _REDIS_FALLBACK.get("host") or "127.0.0.1").strip()
    port = _as_int(cfg.get("port") or _REDIS_FALLBACK.get("port"), 1, 65535, 6379)
    password = cfg.get("password")
    if password is None or str(password).strip() == "":
        password = _REDIS_FALLBACK.get("password") or ""
    db = _as_int(cfg.get("db"), 0, 15, STORAGE_DB_FALLBACK)
    return {"host": host, "port": port, "password": password, "db": db}
