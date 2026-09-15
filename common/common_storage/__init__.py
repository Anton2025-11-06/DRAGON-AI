# -*- coding: utf-8 -*-
"""统一文件存储入口：业务模块只用这里的四个方法（后端 local / 阿里云 OSS 由配置切换）。

    from common import common_storage

    infos = await common_storage.upload(file)                  # 单文件（UploadFile）
    infos = await common_storage.upload(files)                  # 多文件（list[UploadFile]）
    data = await common_storage.download(info["fileName"])      # 输入文件名 → bytes
    if await common_storage.exists(info["fileName"]):
        await common_storage.delete(info["fileName"])

上传返回（每项一个 dict，多文件时逐项独立成败）：
    {"ok": true, "fileName": "存储文件名", "name": "原始文件名.后缀", "size": 字节数,
     "url": "匿名可访问URL", "expiresIn": 3600, "expiresAt": "2026-09-15 12:00:00"}
fileName 是下载/删除/判存在的唯一入参（= {32位hex uuid}_{原始文件名}，见 base.py）；
OSS 后端的 url 是 V4 预签名地址（私有桶可访问、到期失效），本地后端的 url 指向业务模块
自己暴露的匿名下载接口（基址取 storage.public_base，缺省按上传请求的 Host 推导）。

启动初始化（bootstrap / arq worker 各自调一次，进程内共用默认后端）：
    await common_storage.init_storage(yml_config.get("storage", {}))
    await common_storage.close_storage()

配置示例（Nacos yml `storage:` 段，或环境变量兜底；取值优先级 yml 显式值 > 环境变量 > 代码默认，
yml 里留空串视为未配置）：
    storage:
      type: local                   # local(默认) | oss
      url_expires: 3600             # 匿名 URL 有效期（秒）：OSS 预签名有效期 / 本地文件可读期
      # --- type: local ---
      local_dir: data/storage_files # STORAGE_LOCAL_DIR（落盘目录）
      public_base: ''               # 匿名下载接口基址，留空则按上传请求 Host 推导
      # --- type: oss（官方 SDK V2 异步客户端 oss.aio.AsyncClient）---
      region: cn-hangzhou           # OSS_REGION（必填，V4 签名要求与 bucket 地域一致）
      endpoint: ''                  # OSS_ENDPOINT（选填，缺省按 region 构造公网域名）
      access_key: ''                # OSS_ACCESS_KEY_ID（与 mysql/redis 口令同级，直接写本配置）
      secret_key: ''                # OSS_ACCESS_KEY_SECRET
      security_token: ''            # OSS_SESSION_TOKEN（STS 临时凭证才填）
      bucket: dragon-ai             # OSS_BUCKET
      path_prefix: workflow/        # OSS_PATH_PREFIX（对象键前缀，多环境共用 bucket 时区分）
      use_internal_endpoint: false  # OSS_USE_INTERNAL（同地域 ECS 内网免流量费）
      use_accelerate_endpoint: false # OSS_USE_ACCELERATE（传输加速域名）
      use_cname: false              # OSS_USE_CNAME（自定义域名访问）
      use_path_style: false         # OSS_USE_PATH_STYLE
      connect_timeout: ''           # OSS_CONNECT_TIMEOUT（秒，留空用 SDK 默认）
      readwrite_timeout: ''         # OSS_READWRITE_TIMEOUT
      retry_max_attempts: ''        # OSS_RETRY_MAX_ATTEMPTS

后端类型识别优先级：storage.type > 环境变量 STORAGE_BACKEND > local。
配了未知 type 会启动报错而不是静默回到本地盘（「上传写 OSS、另一个进程去本地找文件」最难查）。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, List, Optional, Sequence, Union

from common.common_log.log_init import log
from common.common_storage.base import (
    DEFAULT_URL_EXPIRES, MAX_FILE_SIZE, StorageBackend, check_name, new_file_name,
    original_name,
)
from common.common_storage.local import DEFAULT_LOCAL_DIR, LocalStorageBackend
from common.common_storage.oss import OSSStorageBackend

__all__ = [
    "StorageBackend", "LocalStorageBackend", "OSSStorageBackend",
    "MAX_FILE_SIZE", "init_storage", "close_storage", "get_storage",
    "upload", "download", "delete", "exists", "original_name",
]

_default: Optional[StorageBackend] = None


# ==================== 业务入口（四个方法） ====================

async def upload(files: Union[Any, Sequence[Any]],
                 base_url: Optional[str] = None) -> List[dict]:
    """把一个/多个接口文件上传到当前存储后端，始终返回列表。

    files 为 FastAPI 的 UploadFile（或任何具备 filename/content_type/read()/size 的对象）；
    base_url 是匿名下载接口基址（仅本地后端用，OSS 忽略），由调用方按请求 Host 推导。
    多文件并发上传（asyncio.gather），逐项返回成败，不因一个失败丢掉其它结果。
    """
    backend = get_storage()
    items = files if isinstance(files, (list, tuple)) else [files]
    return list(await asyncio.gather(
        *(_upload_one(f, backend, base_url) for f in items)))


async def download(name: str) -> bytes:
    """按文件名从存储读回全部字节；不存在/已过期抛 ValueError。"""
    return await get_storage().load(check_name(name))


async def delete(name: str) -> bool:
    """按文件名删除；不存在返回 False（幂等）。"""
    return await get_storage().delete(check_name(name))


async def exists(name: str) -> bool:
    """按文件名判断是否存在（已过期算不存在）。"""
    return await get_storage().exists(check_name(name))


async def _upload_one(file, backend: StorageBackend, base_url: Optional[str]) -> dict:
    name = new_file_name(getattr(file, "filename", "") or "")
    display = original_name(name)
    try:
        data = await _read_upload(file)
        await backend.save(name, data, getattr(file, "content_type", None))
        url, expires_in = await backend.public_url(name, base_url)
        return {"ok": True, "fileName": name, "name": display, "size": len(data),
                "url": url, "expiresIn": int(expires_in),
                "expiresAt": (datetime.now() + timedelta(seconds=int(expires_in)))
                .strftime("%Y-%m-%d %H:%M:%S")}
    except (ValueError, RuntimeError) as e:  # 后端契约内的失败（超限/未初始化/权限）
        log.warning(f"upload failed: {display} - {e}")
        return {"ok": False, "fileName": "", "name": display, "size": 0,
                "url": "", "expiresIn": 0, "error": str(e)}


async def _read_upload(file) -> bytes:
    """接口文件 → 字节：先看声明大小，再按块读（超限立即中断，不把超大请求体读满内存）。"""
    declared = getattr(file, "size", None)
    if declared and declared > MAX_FILE_SIZE:
        raise ValueError(f"文件超过大小上限 {MAX_FILE_SIZE // 1024 // 1024}MB")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小上限 {MAX_FILE_SIZE // 1024 // 1024}MB")
        chunks.append(chunk)
    if not total:
        raise ValueError("空文件")
    return b"".join(chunks)


# ==================== 后端初始化 ====================

async def init_storage(config: Optional[dict] = None) -> StorageBackend:
    """按配置初始化默认存储后端（bootstrap 启动时调一次，可替换先前默认实例）。"""
    global _default
    cfg = dict(config or {})
    backend_type = str(
        cfg.get("type") or os.environ.get("STORAGE_BACKEND") or "local"
    ).lower()
    url_expires = _cfg_int(cfg, "url_expires", "STORAGE_URL_EXPIRES") or DEFAULT_URL_EXPIRES
    if backend_type in ("oss", "aliyun", "alioss"):
        backend = OSSStorageBackend()
        await backend.init(
            region=cfg.get("region") or os.environ.get("OSS_REGION"),
            access_key=(cfg.get("access_key") or os.environ.get("OSS_ACCESS_KEY_ID")
                        or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")),
            secret_key=(cfg.get("secret_key") or os.environ.get("OSS_ACCESS_KEY_SECRET")
                        or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")),
            bucket=cfg.get("bucket") or os.environ.get("OSS_BUCKET") or "dragon-ai",
            endpoint=cfg.get("endpoint") or os.environ.get("OSS_ENDPOINT") or None,
            security_token=(cfg.get("security_token")
                            or os.environ.get("OSS_SESSION_TOKEN") or None),
            path_prefix=cfg.get("path_prefix") or os.environ.get("OSS_PATH_PREFIX") or "",
            use_internal_endpoint=_cfg_bool(cfg, "use_internal_endpoint", "OSS_USE_INTERNAL"),
            use_accelerate_endpoint=_cfg_bool(cfg, "use_accelerate_endpoint",
                                              "OSS_USE_ACCELERATE"),
            use_cname=_cfg_bool(cfg, "use_cname", "OSS_USE_CNAME"),
            use_path_style=_cfg_bool(cfg, "use_path_style", "OSS_USE_PATH_STYLE"),
            connect_timeout=_cfg_int(cfg, "connect_timeout", "OSS_CONNECT_TIMEOUT"),
            readwrite_timeout=_cfg_int(cfg, "readwrite_timeout", "OSS_READWRITE_TIMEOUT"),
            retry_max_attempts=_cfg_int(cfg, "retry_max_attempts", "OSS_RETRY_MAX_ATTEMPTS"),
            url_expires=url_expires,
        )
        _default = backend
    elif backend_type in ("local", ""):
        _default = LocalStorageBackend(
            local_dir=cfg.get("local_dir") or os.environ.get("STORAGE_LOCAL_DIR")
                      or DEFAULT_LOCAL_DIR,
            public_base=str(cfg.get("public_base")
                            or os.environ.get("STORAGE_PUBLIC_BASE") or ""),
            url_expires=url_expires,
        )
    else:
        raise ValueError(f"不支持的 storage.type={backend_type}（可选 local | oss；"
                         f"S3/MinIO 后端已移除）")
    log.info(f"storage backend selected: {_default.backend_name}")
    return _default


def get_storage() -> StorageBackend:
    """取默认存储后端；未初始化时自动回退本地后端（OSS 需显式 init_storage 或走 bootstrap）。"""
    global _default
    if _default is None:
        _default = LocalStorageBackend()
    return _default


async def close_storage() -> None:
    """释放默认后端资源（进程停机统一调用，可重复调用）。"""
    global _default
    if _default is not None:
        await _default.close()
        _default = None


# ==================== 配置取值工具 ====================

def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _cfg_bool(cfg: dict, key: str, env_name: str) -> bool:
    """yml 值优先（支持 true/false/"true" 字符串），缺省回退环境变量。"""
    value = cfg.get(key)
    if value is None:
        return _env_bool(env_name)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _cfg_int(cfg: dict, key: str, env_name: str) -> Optional[int]:
    """yml 值优先，缺省回退环境变量，均无则 None（交由调用方用默认值）。"""
    value = cfg.get(key)
    if value in (None, ""):
        value = os.environ.get(env_name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        log.warning(f"storage config {key}={value!r} 不是整数，已忽略")
        return None
