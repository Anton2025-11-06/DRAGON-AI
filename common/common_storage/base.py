# -*- coding: utf-8 -*-
"""统一存储后端抽象基类。

后端实现：
- LocalStorageBackend：本地目录（common/common_storage/local.py）
- S3StorageBackend：MinIO / Ceph RGW / 阿里云 OSS 等 S3 兼容对象存储（common/common_storage/s3.py）

新增存储后端时继承 StorageBackend 并实现全部抽象方法即可（如未来对接
非 S3 协议的天翼云/腾讯云 COS 等，各自写子类，业务侧无感切换）。
所有方法均为 async（同步 SDK 调用由子类用 asyncio.to_thread 包装，不阻塞事件循环）。
"""
from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from typing import Optional

# 单文件大小上限（各后端统一的业务约束）
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


class StorageBackend(ABC):
    """文件存储后端抽象：上传 / 读取 / 本地路径 / 元信息 / 删除 / 临时访问 URL。"""

    # 后端标识：local / s3（配置识别与日志用）
    backend_name: str = "base"

    def __init__(self) -> None:
        self._enabled = False

    @property
    def enabled(self) -> bool:
        """后端是否完成初始化可用（未配置/连接失败时业务侧据此降级或报错）。"""
        return self._enabled

    # ---------- 子类必须实现 ----------

    @abstractmethod
    async def save_bytes(self, data: bytes, filename: str,
                         content_type: Optional[str] = None) -> dict:
        """保存文件字节，返回 {ref, fileId, name, size}。

        fileId：本次保存生成的唯一业务主键（uuid hex，业务侧落库，节点参数引用它）；
        ref：该后端内的文件引用（本地为绝对路径，对象存储为 object_name），
        业务侧同时落库，后续读取/删除凭 ref 操作。
        """

    @abstractmethod
    async def read_bytes(self, ref: str) -> bytes:
        """读取文件内容；ref 无效时抛 ValueError。"""

    @abstractmethod
    async def get_local_path(self, ref: str) -> Optional[str]:
        """取本地可读路径（对象存储需下载到本地缓存）；文件不可达返回 None。

        供文本解析等本地 IO 场景（如 FileUtils.load_and_extract）使用。
        """

    @abstractmethod
    async def exists(self, ref: str) -> bool:
        """引用是否存在。"""

    @abstractmethod
    async def stat(self, ref: str) -> Optional[dict]:
        """元信息 {name, size, contentType, mtime}；不存在返回 None。"""

    @abstractmethod
    async def remove(self, ref: str) -> bool:
        """删除文件；不存在返回 False，成功返回 True。"""

    @abstractmethod
    async def presigned_url(self, ref: str, expires: int = 3600) -> Optional[str]:
        """临时访问 URL（对象存储支持）；后端不支持时返回 None。"""

    # ---------- 基类通用工具 ----------

    @staticmethod
    def safe_filename(filename: str) -> str:
        """仅取 basename，防路径穿越（本地路径拼接与对象命名通用）。"""
        return os.path.basename(filename or "unnamed")

    @staticmethod
    def new_save_args(filename: str, data: bytes) -> tuple[str, str, int]:
        """子类 save_bytes 通用入参处理：返回 (file_id, safe_name, size) 并做大小校验。"""
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小限制（{MAX_FILE_SIZE // 1024 // 1024}MB）")
        return uuid.uuid4().hex, StorageBackend.safe_filename(filename), len(data)

    async def close(self) -> None:
        """释放资源（默认无操作，子类按需重写）。"""