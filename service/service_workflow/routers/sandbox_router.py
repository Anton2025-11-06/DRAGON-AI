# -*- coding: utf-8 -*-
"""
沙箱接口：首页「深度探索 Hernes」对话框 + 右侧沙箱工作区文件浏览器
- 任何登录用户可访问（不绑定业务权限码），网关 JWT 中间件统一鉴权
"""
import urllib.parse

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user
from service.service_workflow.services import sandbox_service as svc

router = APIRouter(prefix="/sandbox", tags=["沙箱"])


class SandboxChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="用户提问")
    skills: list = Field(default_factory=list, description="挂载的技能 id 列表")
    tools: list = Field(default_factory=list, description="挂载的工具 id 列表")
    kbs: list = Field(default_factory=list, description="挂载的知识库 id 列表")


@router.get("/files", summary="沙箱工作区目录列表")
async def sandbox_files(request: Request, path: str = ""):
    login_user = await get_login_user(request)
    try:
        data = svc.list_dir(login_user.get("username") or str(login_user.get("user_id")), path)
        return ApiResponse.success(data=data)
    except svc.SandboxError as e:
        return ApiResponse.error(400, str(e))


@router.get("/file", summary="查看沙箱文本文件内容")
async def sandbox_file(request: Request, path: str = ""):
    login_user = await get_login_user(request)
    try:
        data = svc.read_text_file(login_user.get("username") or str(login_user.get("user_id")), path)
        return ApiResponse.success(data=data)
    except svc.SandboxError as e:
        return ApiResponse.error(400, str(e))


@router.post("/upload", summary="上传文件到沙箱工作区")
async def sandbox_upload(request: Request, path: str = Form(""), file: UploadFile = File(...)):
    login_user = await get_login_user(request)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        return ApiResponse.error(400, "文件超过 50MB 限制")
    try:
        data = svc.save_file(
            login_user.get("username") or str(login_user.get("user_id")),
            path, file.filename, content)
        return ApiResponse.success(data=data, message="上传成功")
    except svc.SandboxError as e:
        return ApiResponse.error(400, str(e))


@router.get("/download", summary="下载沙箱文件或文件夹(zip)")
async def sandbox_download(request: Request, path: str = ""):
    login_user = await get_login_user(request)
    username = login_user.get("username") or str(login_user.get("user_id"))
    try:
        target = svc.resolve_path(username, path)
    except svc.SandboxError as e:
        return ApiResponse.error(400, str(e))
    if not target.exists():
        return ApiResponse.error(400, "文件或文件夹不存在")
    if target.is_file():
        return FileResponse(
            str(target),
            media_type="application/octet-stream",
            filename=target.name)
    buf = svc.zip_dir(target, target.name)
    quoted = urllib.parse.quote(f"{target.name}.zip")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.post("/chat", summary="对话沙箱 AI（演示）")
async def sandbox_chat(request: Request, body: SandboxChatRequest):
    await get_login_user(request)
    data = svc.chat_reply(body.message, body.skills, body.tools, body.kbs)
    return ApiResponse.success(data=data)


@router.get("/skills", summary="沙箱可用技能列表（演示）")
async def sandbox_skills(request: Request):
    await get_login_user(request)
    return ApiResponse.success(data={"items": svc.demo_skills(), "total": len(svc.demo_skills())})