# -*- coding: utf-8 -*-
"""存储后端抽象：只做「按文件名存字节 / 读字节 / 删除 / 判存在 / 给匿名 URL」五件事。

业务侧不要直接用本类，统一用 common.common_storage 的四个入口方法
（upload / download / delete / exists），本文件只被两个后端和入口实现。

契约（新增后端必须遵守）：
1. **name 是唯一的文件标识**：形如 {32位hex uuid}_{原始文件名}，由 new_file_name() 生成。
   后端把它落在自己的位置（本地=local_dir 下，OSS=path_prefix 下），业务侧只透传 name，
   不解析、不拼路径 —— 需要原始展示名时用 original_name(name) 反推。
2. **全异步**：方法一律 async；SDK 只有同步实现时在后端内部用 asyncio.to_thread 包，
   不把同步接口泄漏给业务侧。后端**不提供**「取本地路径」的能力（对象存储要为此把对象
   下到本地缓存，换来缓存失效/磁盘占用/多副本三类新问题）；确实需要文件形态的场景
   （如 LibreOffice 子进程转换）由调用方自己写临时文件并在 finally 删除。
3. **错误语义**：load 的「不存在/已过期」→ ValueError；delete / exists 的「不存在」
   不是错误（返回 False）；未初始化就调用 → RuntimeError（ensure_ready）。
4. **URL 有效期**：public_url 返回 (url, expires_in 秒)。OSS 是预签名 URL（签名自带有效期），
   本地后端是业务模块的下载接口地址（由后端按「写入时间 + 有效期」判定，过期即清理）。
5. close() 进程停机时统一 await，且必须可重复调用；close 后实例视为不可用。
"""
from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from typing import Optional

# 单文件大小上限（上传入口据此校验，先查 UploadFile.size 再校验实际字节数）
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

# 后端未配置 url_expires 时的兜底有效期（秒）
DEFAULT_URL_EXPIRES = 3600

# uuid 前缀长度（new_file_name 用 hex，无连字符）
_ID_LEN = 32


class StorageBackend(ABC):
    """文件存储后端抽象。"""

    # 后端标识：local / oss（配置识别与日志用）
    backend_name: str = "base"

    def __init__(self, url_expires: int = DEFAULT_URL_EXPIRES) -> None:
        self._enabled = False
        self.url_expires = int(url_expires or DEFAULT_URL_EXPIRES)

    # ---------- 子类必须实现 ----------

    @abstractmethod
    async def save(self, name: str, data: bytes,
                   content_type: Optional[str] = None) -> None:
        """按文件名写入字节（同名直接覆盖，正常情况下 name 唯一不会撞）。"""

    @abstractmethod
    async def load(self, name: str) -> bytes:
        """读文件全部字节（内存直返）；不存在/已过期抛 ValueError。"""

    @abstractmethod
    async def exists(self, name: str) -> bool:
        """文件是否存在（已过期视为不存在，不抛异常）。"""

    @abstractmethod
    async def delete(self, name: str) -> bool:
        """删除文件；不存在返回 False，成功返回 True（幂等）。"""

    @abstractmethod
    async def public_url(self, name: str,
                         base_url: Optional[str] = None) -> tuple[str, int]:
        """匿名可访问 URL + 有效期（秒）。

        base_url 由调用方按当前请求推导（内网/公网域名运行时才知道），
        后端自身配置优先；OSS 忽略该参数，直接给预签名地址。
        """

    # ---------- 基类通用能力 ----------

    def ensure_ready(self) -> None:
        """每个入口方法第一行调用（本地后端无外部依赖，init 后即 True）。"""
        if not self._enabled:
            raise RuntimeError(
                f"{self.backend_name} 存储后端未初始化（请检查 Nacos storage 配置段）")

    async def close(self) -> None:
        """释放资源（默认无操作，持有连接/会话的子类按需重写）。"""


# ==================== 命名与校验（各后端共用） ====================

def new_file_name(original: str) -> str:
    """原始文件名 → 唯一存储名 {uuid hex}_{安全文件名}。

    只保留 basename（客户端带路径时丢掉目录），并拦掉路径分隔符/控制字符，
    使存储名在任何后端都是一层扁平名字，不存在目录穿越。
    """
    base = os.path.basename((original or "").replace("\\", "/")).strip()
    safe = "".join(ch for ch in base if ch >= " " and ch not in '"<>|?*').strip()
    if len(safe) > 180:  # 保住扩展名的同时防止名字过长
        stem, ext = os.path.splitext(safe)
        safe = stem[:180 - len(ext)] + ext
    return f"{uuid.uuid4().hex[:_ID_LEN]}_{safe or 'unnamed'}"


def original_name(name: str) -> str:
    """存储名 → 原始文件名（剥掉 uuid 前缀；不是本工具生成的名字则原样返回）。"""
    name = name or ""
    head, sep, tail = name.partition("_")
    if sep and len(head) == _ID_LEN and all(c in "0123456789abcdef" for c in head.lower()):
        return tail or name
    return name


def check_name(name: str) -> str:
    """校验文件名合法性（下载/删除/判存在都以此为准入口，防路径穿越）。"""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or os.path.basename(name) != name:
        raise ValueError(f"非法文件名: {name!r}")
    return name
