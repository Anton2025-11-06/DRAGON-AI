# -*- coding: utf-8 -*-
"""本地目录存储后端：开发/单机自测用，生产用 OSS 后端。

文件落在 local_dir 扁平目录下，文件名即存储名（{uuid}_{原始名}），不建子目录、
不写 sidecar 元信息 —— 展示名从文件名本身反推（base.original_name），MIME 由下载
接口按扩展名猜。

「匿名可访问 URL」本地盘自己提供不了，只能指向业务模块暴露的下载接口：
    {public_base}/{urlencoded 文件名}
public_base 优先取 storage.public_base 配置（运维口径，跨域名稳定），缺省用调用方
按当前请求推导的 base_url（见 service_workflow 的 workflow-files 路由）。

URL 有效期落地：以文件写入时间（mtime）+ url_expires 判定，读取时已过期则顺手删除，
让本地临时文件自动回收（与 OSS 预签名 URL 过期即不可访问的语义对齐）。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional
from urllib.parse import quote

from common.common_log.log_init import log
from common.common_storage.base import (
    DEFAULT_URL_EXPIRES, StorageBackend, check_name,
)

# 默认落盘目录（相对项目根，可由 storage.local_dir / 环境变量 STORAGE_LOCAL_DIR 覆盖）
DEFAULT_LOCAL_DIR = os.path.join("data", "storage_files")


class LocalStorageBackend(StorageBackend):
    """本地磁盘后端：文件名为唯一标识，目录懒创建，无外部依赖。"""

    backend_name = "local"

    def __init__(self, local_dir: str = DEFAULT_LOCAL_DIR, public_base: str = "",
                 url_expires: int = DEFAULT_URL_EXPIRES) -> None:
        super().__init__(url_expires=url_expires)
        self._dir = os.path.abspath(local_dir or DEFAULT_LOCAL_DIR)
        self._public_base = (public_base or "").rstrip("/")
        os.makedirs(self._dir, exist_ok=True)
        self._enabled = True
        log.info(f"local storage ready: dir={self._dir} "
                 f"public_base={self._public_base or '(按上传请求 Host 推导)'} "
                 f"url_expires={self.url_expires}s")

    async def save(self, name: str, data: bytes,
                   content_type: Optional[str] = None) -> None:
        path = self._path(name)
        await asyncio.to_thread(self._write, path, data)

    async def load(self, name: str) -> bytes:
        path = self._path(name)
        if self._expired(path):
            await asyncio.to_thread(self._remove, path)  # 过期即回收，不留垃圾
            raise ValueError(f"文件已过期: {name}")
        if not os.path.isfile(path):
            raise ValueError(f"文件不存在: {name}")
        return await asyncio.to_thread(self._read, path)

    async def exists(self, name: str) -> bool:
        path = self._path(name)
        if not os.path.isfile(path):
            return False
        if self._expired(path):  # 过期视为不存在，并就地回收
            await asyncio.to_thread(self._remove, path)
            return False
        return True

    async def delete(self, name: str) -> bool:
        return await asyncio.to_thread(self._remove, self._path(name))

    async def public_url(self, name: str,
                         base_url: Optional[str] = None) -> tuple[str, int]:
        base = self._public_base or (base_url or "").rstrip("/")
        if not base:
            raise ValueError("本地存储需要配置 storage.public_base（匿名下载接口基址）")
        # 文件名可含中文/空格，URL 里必须转义（下载接口由框架自动解码还原）
        return f"{base}/{quote(name)}", self.url_expires

    # ---------- 内部工具 ----------

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, check_name(name))

    def _expired(self, path: str) -> bool:
        """mtime + 有效期 < 当前时间 即过期（文件不存在时按未过期处理，交由上层报「不存在」）。"""
        mtime = get_mtime(path)
        return bool(mtime) and time.time() - mtime > self.url_expires

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _remove(path: str) -> bool:
        try:
            os.remove(path)
            return True
        except OSError:
            return False


def get_mtime(path: str) -> float:
    """文件修改时间（秒）；不存在返回 0。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
