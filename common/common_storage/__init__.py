# -*- coding: utf-8 -*-
"""统一文件存储后端：本地目录 / S3 兼容对象存储（MinIO、Ceph RGW、阿里云 OSS 等）。

用法：
    from common.common_storage import get_storage, init_storage

    # bootstrap 启动时按 Nacos yml `storage:` 段初始化一次
    await init_storage(yml_config.get("storage", {}))

    # 业务侧每次现场取默认后端（实例可能被 init_storage 切换，不要模块级缓存）
    storage = get_storage()
    info = await storage.save_bytes(data, "a.txt")   # {"ref": ..., "name": ..., "size": ...}

配置示例（Nacos yml `storage:` 段，或环境变量兜底）：
    storage:
      type: s3                      # local(默认) | s3
      endpoint: 127.0.0.1:9000      # MINIO_ENDPOINT
      access_key: minioadmin        # MINIO_ACCESS_KEY
      secret_key: minioadmin        # MINIO_SECRET_KEY
      bucket: dragon-ai             # MINIO_BUCKET（默认 dragon-ai）
      secure: false                 # MINIO_SECURE（true/false）

后端类型识别优先级：storage.type > 环境变量 STORAGE_BACKEND；
未配置任何 s3 参数时默认本地后端（无外部依赖，永不失败）。
"""
from __future__ import annotations

import os
from typing import Optional

from common.common_log.log_init import log
from common.common_storage.base import MAX_FILE_SIZE, StorageBackend
from common.common_storage.local import LocalStorageBackend
from common.common_storage.s3 import S3StorageBackend

__all__ = [
    "StorageBackend", "LocalStorageBackend", "S3StorageBackend",
    "MAX_FILE_SIZE", "init_storage", "get_storage",
]

_default: Optional[StorageBackend] = None


async def init_storage(config: Optional[dict] = None) -> StorageBackend:
    """按配置初始化默认存储后端（bootstrap 启动时调用一次，可替换先前默认实例）。"""
    global _default
    cfg = dict(config or {})
    backend_type = str(
        cfg.get("type") or os.environ.get("STORAGE_BACKEND") or "local"
    ).lower()
    if backend_type == "s3":
        backend = S3StorageBackend()
        await backend.init(
            endpoint=cfg.get("endpoint") or os.environ.get("MINIO_ENDPOINT"),
            access_key=cfg.get("access_key") or os.environ.get("MINIO_ACCESS_KEY"),
            secret_key=cfg.get("secret_key") or os.environ.get("MINIO_SECRET_KEY"),
            bucket=cfg.get("bucket") or os.environ.get("MINIO_BUCKET") or "dragon-ai",
            secure=cfg.get("secure", False) or _env_bool("MINIO_SECURE"),
        )
        _default = backend
    else:
        _default = LocalStorageBackend()
    log.info(f"storage backend selected: {_default.backend_name}")
    return _default


def get_storage() -> StorageBackend:
    """取默认存储后端；未初始化时自动回退本地后端（s3 需显式 init_storage 或走 bootstrap）。"""
    global _default
    if _default is None:
        _default = LocalStorageBackend()
    return _default


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")