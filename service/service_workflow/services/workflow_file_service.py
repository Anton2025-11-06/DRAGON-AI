# -*- coding: utf-8 -*-
"""工作流文件服务：上传 / 批量上传 / 信息 / 删除。

供 DOC_EXTRACTOR 等节点引用的临时文件（画布上传 → file_id → 节点参数引用）。
文件存储经统一存储抽象 common.common_storage（本地 / MinIO 等 S3 后端，配置即切换）；
文本解析在 common.common_file（FileUtils）；本服务只负责业务元数据
（tb_workflow_file 表）与文件引用的绑定。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import UploadFile

from common.common_file.file_utils import FileUtils
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_storage import get_storage
from service.service_workflow.models.workflow_entity import WorkflowFile


class WorkflowFileService:

    @staticmethod
    async def upload(file: UploadFile, user_id: int = 0) -> dict:
        data = await file.read()
        stored = await get_storage().save_bytes(data, file.filename,
                                                content_type=file.content_type)
        content_type = file.content_type or "application/octet-stream"
        async with mysql_client.get_session() as session:
            session.add(WorkflowFile(
                file_id=stored["fileId"], name=stored["name"], size=stored["size"],
                content_type=content_type,
                storage_path=stored["ref"], user_id=user_id))
            await session.commit()
        return FileUtils.dto(stored["fileId"], stored["name"], stored["size"], content_type)

    @staticmethod
    async def batch_upload(files: list[UploadFile], user_id: int = 0) -> list[dict]:
        return [await WorkflowFileService.upload(f, user_id) for f in files]

    @staticmethod
    async def info(file_id: str) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowFile, file_id)
            if row is None:
                return None
            return FileUtils.dto(row.file_id, row.name, row.size, row.content_type,
                                 row.create_time)

    @staticmethod
    async def get_local_path(file_id: str) -> Optional[str]:
        """供 DOC_EXTRACTOR 的 file_loader 钩子取本地路径。

        本地后端直接返回文件路径；S3 后端下载到本地缓存后返回（重复调用复用缓存）。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowFile, file_id)
            if row is None:
                return None
            return await get_storage().get_local_path(row.storage_path)

    @staticmethod
    async def load_and_extract(file_ref: str,
                               supported_types: Optional[list] = None) -> tuple[str, dict]:
        """DOC_EXTRACTOR 的 file_loader 实现：文件引用 → (文本内容, 元数据)。

        文件引用支持：workflow-files 上传的 fileId（DB 定位存储引用）或本地路径；
        一期不做 URL 远端抓取（URL 会走到「文件不存在」报错）。
        能力实现见 common.common_file.file_utils.FileUtils。
        """
        db_path = await WorkflowFileService.get_local_path(file_ref)
        if not db_path and os.path.exists(file_ref):
            # 本地路径直用：无 file_id 前缀，原文件名即 basename
            return await FileUtils.load_and_extract(file_ref, file_id=None,
                                                    supported_types=supported_types)
        if not db_path:
            raise ValueError(f"文件不存在或不可访问: {file_ref}")
        return await FileUtils.load_and_extract(db_path, file_id=file_ref,
                                                supported_types=supported_types)

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
            await get_storage().remove(storage_path)
        except (OSError, ValueError):
            log.warning(f"workflow file remove missing: {storage_path}")
        return True