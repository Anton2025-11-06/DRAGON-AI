# -*- coding: utf-8 -*-
"""本地目录存储后端：开发/单机自测用，生产用 OSS 后端。

文件落在 local_dir 下，文件名即存储名（{uuid}_{原始名}）；name 可带一段安全子目录
（如 skills/xxx.zip）则懒建到 local_dir/skills/ 下，仍不写 sidecar 元信息 ——
展示名从文件名本身反推（base.original_name），MIME 由下载接口按扩展名猜。

「匿名可访问 URL」本地盘自己提供不了，只能指向业务模块暴露的下载接口：
    {public_base}/{urlencoded 文件名}
public_base 优先取 storage.public_base 配置（运维口径，跨域名稳定），缺省用调用方
按当前请求推导的 base_url（见 service_workflow 的 workflow-files 路由）。

存储语义：**本地文件一律永久保存**，不再按 mtime 过期回收（与 OSS 对齐——OSS 对象
本身永久，预签名到期只影响 URL 可访问性、不删对象）。url_expires 仅作为 public_url 的
返回值保留，不再据此删除文件；因此需要长期留存的资产（如技能 zip）可安全落在本地后端。
"""
from __future__ import annotations

import asyncio
import os
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
        """永久保存：只判存在，不再按 mtime 过期回收。"""
        path = self._path(name)
        if not os.path.isfile(path):
            raise ValueError(f"文件不存在: {name}")
        return await asyncio.to_thread(self._read, path)

    async def exists(self, name: str) -> bool:
        return await asyncio.to_thread(os.path.isfile, self._path(name))

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
        root = self._dir
        path = os.path.normpath(os.path.join(root, check_name(name)))
        # 纵深防御：即便校验被绕过，拼接后的绝对路径也必须仍在 root 目录内（防目录穿越）
        if path != root and not path.startswith(root + os.sep):
            raise ValueError(f"非法文件名: {name!r}")
        return path

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
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
