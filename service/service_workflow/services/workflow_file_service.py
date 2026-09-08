# -*- coding: utf-8 -*-
"""工作流文件服务：上传 / 批量上传 / 信息 / 删除。

供 DOC_EXTRACTOR 等节点引用的临时文件（画布上传 → file_id → 节点参数引用）。
存储位置：环境变量 WORKFLOW_FILE_DIR（默认 ./data/workflow_files），元数据落 tb_workflow_file。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import UploadFile

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.models.workflow_entity import WorkflowFile

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

_FILE_DIR = None


def _file_dir() -> str:
    global _FILE_DIR
    if _FILE_DIR is None:
        _FILE_DIR = os.environ.get("WORKFLOW_FILE_DIR", os.path.join("data", "workflow_files"))
        os.makedirs(_FILE_DIR, exist_ok=True)
    return _FILE_DIR


def _write_sync(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


class WorkflowFileService:

    @staticmethod
    async def upload(file: UploadFile, user_id: int = 0) -> dict:
        data = await file.read()
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小限制（{MAX_FILE_SIZE // 1024 // 1024}MB）")
        file_id = uuid.uuid4().hex
        # 存储名统一 file_id 前缀，避免同名覆盖与路径穿越
        safe_name = os.path.basename(file.filename or "unnamed")
        storage_path = os.path.join(_file_dir(), f"{file_id}_{safe_name}")
        await asyncio.to_thread(_write_sync, storage_path, data)
        async with mysql_client.get_session() as session:
            session.add(WorkflowFile(
                file_id=file_id, name=safe_name, size=len(data),
                content_type=file.content_type or "application/octet-stream",
                storage_path=storage_path, user_id=user_id))
            await session.commit()
        return WorkflowFileService._dto(file_id, safe_name, len(data),
                                        file.content_type or "application/octet-stream")

    @staticmethod
    async def batch_upload(files: list[UploadFile], user_id: int = 0) -> list[dict]:
        return [await WorkflowFileService.upload(f, user_id) for f in files]

    @staticmethod
    def _dto(file_id: str, name: str, size: int, content_type: str,
             upload_time: Optional[datetime] = None) -> dict:
        return {"fileId": file_id, "name": name, "size": size,
                "contentType": content_type,
                "uploadTime": (upload_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")}

    @staticmethod
    async def info(file_id: str) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowFile, file_id)
            if row is None:
                return None
            return {"fileId": row.file_id, "name": row.name, "size": row.size,
                    "contentType": row.content_type,
                    "uploadTime": row.create_time.strftime("%Y-%m-%d %H:%M:%S")}

    @staticmethod
    async def get_local_path(file_id: str) -> Optional[str]:
        """供 DOC_EXTRACTOR 的 file_loader 钩子取本地路径。"""
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowFile, file_id)
            if row is None:
                return None
            return row.storage_path if os.path.exists(row.storage_path) else None

    @staticmethod
    async def delete(file_id: str) -> bool:
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowFile, file_id)
            if row is None:
                return False
            storage_path = row.storage_path
            await session.delete(row)
            await session.commit()
        try:
            await asyncio.to_thread(os.remove, storage_path)
        except OSError:
            log.warning(f"workflow file remove missing: {storage_path}")
        return True
