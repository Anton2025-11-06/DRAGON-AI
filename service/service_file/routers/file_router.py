# -*- coding: utf-8 -*-
"""文件微服务路由：上传 / 下载（暂不做鉴权，供大模型按可访问 URL 拉取）。

- POST /file/upload    多部分上传，落统一存储后端，返回 {fileId, name, size, url}
- GET  /file/download/{file_id}  按 file_id 读回文件字节流

file_id → 存储 ref 的映射用本地 sidecar 索引（_file_index.json）维护，
兼容 local/s3 后端（save 返回的 ref 落索引，download 凭 ref 读取）。
返回的 url 为经网关对外可达路径：{public_base}/api/file/file/download/{file_id}。
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response

from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from common.common_storage import get_storage

router = APIRouter(prefix="/file", tags=["文件服务"])

# 索引文件目录：与本地存储后端同根（WORKFLOW_FILE_DIR 默认 data/workflow_files）
_INDEX_DIR = os.environ.get("WORKFLOW_FILE_DIR", os.path.join("data", "workflow_files"))
_INDEX_PATH = os.path.join(os.path.abspath(_INDEX_DIR), "_file_index.json")

_index_lock = asyncio.Lock()


def _public_base(request: Request) -> str:
    """对外基址：优先环境变量 file_public_base，缺省回退请求 Host（内网可达即可，公网转发由运维处理）。"""
    base = os.environ.get("file_public_base")
    if base:
        return base.rstrip("/")
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


async def _load_index() -> dict:
    if not os.path.exists(_INDEX_PATH):
        return {}
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning(f"file index load failed, fallback to empty: {_INDEX_PATH}")
        return {}


async def _save_index(index: dict) -> None:
    os.makedirs(os.path.dirname(_INDEX_PATH), exist_ok=True)
    tmp = _INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    os.replace(tmp, _INDEX_PATH)


@router.post("/upload", summary="上传文件，返回可下载 URL")
async def upload_file(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        return ApiResponse.error(400, "空文件")
    content_type: Optional[str] = file.content_type
    info = await get_storage().save_bytes(data, file.filename or "unnamed", content_type)
    file_id = info["fileId"]
    async with _index_lock:
        index = await _load_index()
        index[file_id] = {
            "ref": info["ref"],
            "name": info["name"],
            "size": info["size"],
            "contentType": content_type,
        }
        await _save_index(index)
    url = f"{_public_base(request)}/api/file/file/download/{file_id}"
    log.info(f"file uploaded: {file_id} ({info['name']}, {info['size']}B)")
    return ApiResponse.success(data={
        "fileId": file_id, "name": info["name"], "size": info["size"], "url": url,
    }, message="上传成功")


@router.get("/download/{file_id}", summary="按 file_id 下载文件")
async def download_file(file_id: str):
    # 防路径穿越：file_id 应为 uuid hex
    if not file_id or any(sep in file_id for sep in ("/", "\\", "..")):
        return ApiResponse.error(400, "非法 file_id")
    index = await _load_index()
    meta = index.get(file_id)
    if not meta:
        return ApiResponse.error(404, "文件不存在")
    try:
        data = await get_storage().read_bytes(meta["ref"])
    except ValueError as e:
        log.warning(f"file read failed {file_id}: {e}")
        return ApiResponse.error(404, "文件不存在")
    # Content-Disposition 头部按 latin-1 编码，中文等非 ASCII 文件名需回退名 + RFC 5987 filename*
    name = meta.get("name") or file_id
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip() or file_id
    disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(
        content=data,
        media_type=meta.get("contentType") or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )
