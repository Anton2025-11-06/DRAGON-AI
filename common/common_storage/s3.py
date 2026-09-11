# -*- coding: utf-8 -*-
"""S3 兼容对象存储后端：MinIO / Ceph RGW / 阿里云 OSS 等。

配置（Nacos yml `storage:` 段或环境变量，见 common_storage/__init__.py）：
- endpoint / access_key / secret_key / bucket / secure
环境变量兜底：MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET / MINIO_SECURE。

ref = object_name（{file_id}_{原文件名}，与本地后端命名约定一致，业务 DB 无感切换）。
get_local_path 会把对象下载到本地缓存目录（STORAGE_CACHE_DIR，默认 ./data/storage_cache），
供文本解析等本地 IO 场景使用；已下载过的对象直接复用缓存。
"""
from __future__ import annotations

import asyncio
import io
import os
from datetime import datetime
from typing import Optional

from minio import Minio
from minio.error import S3Error

from common.common_log.log_init import log
from common.common_storage.base import StorageBackend

_CACHE_DIR = None


def _cache_dir() -> str:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = os.environ.get("STORAGE_CACHE_DIR", os.path.join("data", "storage_cache"))
        os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


class S3StorageBackend(StorageBackend):
    """对象存储后端（S3 协议统一）：ref = object_name。"""

    backend_name = "s3"

    def __init__(self) -> None:
        super().__init__()
        self._client: Optional[Minio] = None
        self._bucket: str = "dragon-ai"

    @property
    def client(self) -> Optional[Minio]:
        return self._client

    async def init(self, endpoint: str, access_key: str, secret_key: str,
                   bucket: str = "dragon-ai", secure: bool = False,
                   region: Optional[str] = None) -> None:
        """建立客户端并确保 bucket 存在；初始化失败抛错（由 bootstrap 记录并阻断启动）。"""
        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=bool(secure),
            region=region,
        )
        self._bucket = bucket or "dragon-ai"
        if not await asyncio.to_thread(self._client.bucket_exists, self._bucket):
            await asyncio.to_thread(self._client.make_bucket, self._bucket)
            log.info(f"S3 storage bucket auto-created: {self._bucket}")
        self._enabled = True
        log.info(f"S3 storage backend ready: {endpoint} bucket={self._bucket} secure={bool(secure)}")

    async def close(self) -> None:
        # minio 客户端无连接池需要显式释放，保持空实现（对齐基类契约）
        pass

    # ---------- 抽象实现 ----------

    async def save_bytes(self, data: bytes, filename: str,
                         content_type: Optional[str] = None) -> dict:
        self._require_ready()
        file_id, safe_name, size = self.new_save_args(filename, data)
        object_name = f"{file_id}_{safe_name}"
        await asyncio.to_thread(
            self._client.put_object, self._bucket, object_name,
            io.BytesIO(data), size, content_type=content_type,
        )
        return {"ref": object_name, "fileId": file_id, "name": safe_name, "size": size}

    async def read_bytes(self, ref: str) -> bytes:
        self._require_ready()
        try:
            return await asyncio.to_thread(self._get_sync, ref)
        except S3Error as e:
            raise ValueError(self._s3_err(ref, "读取", e)) from e

    async def get_local_path(self, ref: str) -> Optional[str]:
        self._require_ready()
        cache = os.path.join(_cache_dir(), ref)
        if os.path.exists(cache):
            return cache
        try:
            data = await asyncio.to_thread(self._get_sync, ref)
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise ValueError(self._s3_err(ref, "下载", e)) from e
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        await asyncio.to_thread(self._write_cache, cache, data)
        return cache

    async def exists(self, ref: str) -> bool:
        self._require_ready()
        try:
            await asyncio.to_thread(self._client.stat_object, self._bucket, ref)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise ValueError(self._s3_err(ref, "查询", e)) from e

    async def stat(self, ref: str) -> Optional[dict]:
        self._require_ready()
        try:
            st = await asyncio.to_thread(self._client.stat_object, self._bucket, ref)
            mtime = None
            if getattr(st, "last_modified", None):
                if isinstance(st.last_modified, datetime):
                    mtime = int(st.last_modified.timestamp())
                else:
                    mtime = int(st.last_modified)
            return {"name": os.path.basename(ref), "size": st.size,
                    "contentType": st.content_type, "mtime": mtime}
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise ValueError(self._s3_err(ref, "查询", e)) from e

    async def remove(self, ref: str) -> bool:
        self._require_ready()
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, ref)
            return True
        except S3Error as e:
            # remove_object 对不存在对象是幂等的，正常不会走到 NoSuchKey
            raise ValueError(self._s3_err(ref, "删除", e)) from e

    async def presigned_url(self, ref: str, expires: int = 3600) -> Optional[str]:
        self._require_ready()
        return await asyncio.to_thread(
            self._client.presigned_get_object, self._bucket, ref, expires=expires)

    # ---------- 内部工具 ----------

    def _require_ready(self) -> None:
        if not self._enabled or self._client is None:
            raise RuntimeError("S3 存储后端未初始化（请检查 storage 配置）")

    @staticmethod
    def _s3_err(ref: str, action: str, e: S3Error) -> str:
        return f"S3 对象{action}失败: {ref} - {e}"

    def _get_sync(self, ref: str) -> bytes:
        """同步下载对象内容（在 to_thread 中执行）。"""
        resp = self._client.get_object(self._bucket, ref)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    @staticmethod
    def _write_cache(path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)