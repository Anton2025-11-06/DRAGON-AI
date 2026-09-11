# -*- coding: utf-8 -*-
"""本地目录存储后端：文件落盘到 WORKFLOW_FILE_DIR（默认 ./data/workflow_files）。

与历史实现（workflow_file_service 内嵌逻辑）保持命名约定与路径语义完全兼容：
存储名 {file_id}_{原文件名}，ref 即本地绝对路径；历史相对路径记录可被规整为绝对路径。
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from common.common_storage.base import StorageBackend

_DIR = None


def _file_dir() -> str:
    global _DIR
    if _DIR is None:
        _DIR = os.environ.get("WORKFLOW_FILE_DIR", os.path.join("data", "workflow_files"))
        os.makedirs(_DIR, exist_ok=True)
    return _DIR


class LocalStorageBackend(StorageBackend):
    """本地磁盘存储：ref = 文件绝对路径。"""

    backend_name = "local"

    def __init__(self) -> None:
        super().__init__()
        # 本地存储总是可用（目录懒创建，无外部依赖）
        self._enabled = True

    async def save_bytes(self, data: bytes, filename: str,
                         content_type: Optional[str] = None) -> dict:
        file_id, safe_name, size = self.new_save_args(filename, data)
        storage_path = os.path.abspath(os.path.join(_file_dir(), f"{file_id}_{safe_name}"))
        await asyncio.to_thread(self._write_sync, storage_path, data)
        return {"ref": storage_path, "fileId": file_id, "name": safe_name, "size": size}

    @staticmethod
    def _write_sync(path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    @staticmethod
    def _read_sync(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def storage_abs(self, storage_path: str) -> str:
        """把落库的相对路径规整为绝对路径（历史记录兼容：CWD / 项目根 依次尝试）。"""
        if not storage_path:
            return ""
        if os.path.isabs(storage_path):
            return storage_path
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # common_storage → common → 项目根
        root = os.path.dirname(root)
        for base in (os.getcwd(), root):
            cand = os.path.abspath(os.path.join(base, storage_path))
            if os.path.exists(cand):
                return cand
        return os.path.abspath(os.path.join(os.getcwd(), storage_path))

    async def read_bytes(self, ref: str) -> bytes:
        path = self.storage_abs(ref)
        if not path or not os.path.exists(path):
            raise ValueError(f"文件不存在: {ref}")
        return await asyncio.to_thread(self._read_sync, path)

    async def get_local_path(self, ref: str) -> Optional[str]:
        path = self.storage_abs(ref)
        return path if path and os.path.exists(path) else None

    async def exists(self, ref: str) -> bool:
        path = self.storage_abs(ref)
        return bool(path) and os.path.exists(path)

    async def stat(self, ref: str) -> Optional[dict]:
        path = self.storage_abs(ref)
        if not path or not os.path.exists(path):
            return None
        st = os.stat(path)
        return {"name": os.path.basename(path), "size": st.st_size,
                "contentType": None, "mtime": int(st.st_mtime)}

    async def remove(self, ref: str) -> bool:
        path = self.storage_abs(ref)
        if not path or not os.path.exists(path):
            return False
        try:
            await asyncio.to_thread(os.remove, path)
            return True
        except OSError:
            return False

    async def presigned_url(self, ref: str, expires: int = 3600) -> Optional[str]:
        # 本地后端无公开访问 URL，返回 None（业务侧自行降级）
        return None