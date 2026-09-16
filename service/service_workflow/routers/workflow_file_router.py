import mimetypes
import os
from typing import List
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import Response

from common import common_storage
from common.common_entity.response_schema import ApiResponse

# ==================== 文件（存储能力全在 common_storage，本路由只做形参适配） ====================

file_router = APIRouter(prefix="/workflow-files", tags=["工作流文件"])

# 匿名下载入口路径（网关对外口径，与 TokenCheckMiddleware 白名单一致）
_DOWNLOAD_PATH = "/api/workflow/workflow-files/download"


def _download_base(request: Request) -> str:
    """本地存储后端的匿名下载基址：环境变量 FILE_PUBLIC_BASE > 请求 Origin/Referer > Host。

    网关不透传原始 Host（下游看到的是实例内网地址），所以优先用浏览器带过来的对外地址；
    公网/CDN 场景用 FILE_PUBLIC_BASE 固定。OSS 后端用预签名 URL，不依赖本基址。
    """
    base = os.environ.get("FILE_PUBLIC_BASE", "").rstrip("/")
    if not base:
        origin = urlsplit(request.headers.get("origin")
                          or request.headers.get("referer") or "")
        base = (f"{origin.scheme}://{origin.netloc}" if origin.scheme and origin.netloc
                else f"{request.url.scheme}://{request.url.netloc}")
    return f"{base}{_DOWNLOAD_PATH}"


@file_router.post("/upload", summary="上传单个文件，返回匿名可访问 URL")
async def upload_file(request: Request, file: UploadFile = File(...)):
    info = (await common_storage.upload(file, base_url=_download_base(request)))[0]
    if not info["ok"]:
        return ApiResponse.error(400, info.get("error") or "上传失败")
    return ApiResponse.success(data=info, message="上传成功")


@file_router.post("/upload-batch", summary="批量上传文件")
async def upload_files(request: Request, files: List[UploadFile] = File(...)):
    infos = await common_storage.upload(files, base_url=_download_base(request))
    failed = [i for i in infos if not i["ok"]]
    return ApiResponse.success(
        data=infos,
        message=f"成功 {len(infos) - len(failed)} 个" +
                (f"，失败 {len(failed)} 个：{failed[0]['error']}" if failed else ""))


@file_router.get("/download/{name}", summary="按文件名下载（匿名，地址由上传接口返回）")
async def download_file(name: str):
    """本地后端的匿名下载入口（OSS 预签名 URL 不经过这里，但同样支持手动访问）。"""
    try:
        data = await common_storage.download(name)
    except ValueError as e:
        return ApiResponse.error(404, str(e))
    display = common_storage.original_name(name)
    # Content-Disposition 头部按 latin-1 编码，中文名需回退名 + RFC 5987 filename*
    ascii_name = display.encode("ascii", "ignore").decode("ascii").strip() or "download"
    return Response(
        content=data,
        media_type=mimetypes.guess_type(display)[0] or "application/octet-stream",
        headers={"Content-Disposition":
                     f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(display)}'})


@file_router.get("/exists/{name}", summary="按文件名判断是否存在")
async def file_exists(name: str):
    return ApiResponse.success(data=await common_storage.exists(name))


@file_router.delete("/{name}", summary="按文件名删除")
async def delete_file(name: str):
    try:
        ok = await common_storage.delete(name)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(message="删除成功" if ok else "文件不存在")
