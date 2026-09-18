# -*- coding: utf-8 -*-
"""
技能管理接口：SKILL.zip 上传 / 替换 / 预览 / 下载 / 重命名 / 状态 / 删除 + 单文件编辑
权限：workflow:skill:list / add / edit / delete
"""
import io
import json
import urllib.parse
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_workflow.models.skill import SkillRenameRequest, SkillFileUpdateRequest
from service.service_workflow.services import skill_service as svc

router = APIRouter(prefix="/skills", tags=["技能管理"])

def _parse_tags_form(raw: str) -> List[str]:
    """Form 标签解析：优先 JSON 数组字符串，兼容逗号分隔"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if str(t).strip()]
        except json.JSONDecodeError:
            pass
    return [t.strip() for t in raw.split(",") if t.strip()]




@router.get("/page", summary="分页查询技能目录")
@has_permission("workflow:skill:list")
async def page_skills(request: Request, current: int = 1, size: int = 10,
                      keyword: str = None, category: str = None, status: bool = None):
    data = await svc.SkillService.page(current, size, keyword, category,status)
    return ApiResponse.success(data=data)


@router.get("/{skill_id}/detail", summary="技能元信息")
@has_permission("workflow:skill:list")
async def skill_detail(request: Request, skill_id: int):
    try:
        data = await svc.SkillService.detail(skill_id)
        return ApiResponse.success(data=data)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/{skill_id}/preview", summary="技能预览（SKILL.md + 资源树 + 文本内容）")
@has_permission("workflow:skill:view")
async def skill_preview(request: Request, skill_id: int):
    try:
        data = await svc.SkillService.preview(skill_id)
        return ApiResponse.success(data=data)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.post("/upload", summary="上传 SKILL.zip 技能包")
@has_permission("workflow:skill:add")
async def upload_skill(request: Request, name: str = Form(...), code: str = Form(...),
                       file: UploadFile = File(...), description: str = Form(""),
                       category: str = Form(""), icon: str = Form(""), tags: str = Form("")):
    login_user = await get_login_user(request)
    data = await file.read()
    try:
        new_id = await svc.SkillService.upload(
            name=name, code=code, data=data,
            description=description or None, category=category or None, icon=icon or None,
            tags=_parse_tags_form(tags), created_by=login_user.get("user_id") or 0,
            original_name=file.filename)
        return ApiResponse.success(data={"id": new_id}, message="技能上传成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{skill_id}/upload", summary="zip 替换技能目录（清空原目录后解压）")
@has_permission("workflow:skill:replace")
async def replace_skill(request: Request, skill_id: int, file: UploadFile = File(...),
                        name: str = Form(""), description: str = Form(""),
                        category: str = Form(""), icon: str = Form(""), tags: str = Form("")):
    data = await file.read()
    try:
        await svc.SkillService.replace(
            skill_id, data,
            name=name or None, description=description or None,
            category=category or None, icon=icon or None, tags=_parse_tags_form(tags),
            original_name=file.filename)
        return ApiResponse.success(message="技能目录已替换")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.patch("/{skill_id}/rename", summary="重命名技能")
@has_permission("workflow:skill:rename")
async def rename_skill(request: Request, skill_id: int, body: SkillRenameRequest):
    try:
        await svc.SkillService.rename(skill_id, body.name)
        return ApiResponse.success(message="重命名成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.patch("/{skill_id}/status", summary="切换启用状态")
@has_permission("workflow:skill:edit")
async def toggle_status(request: Request, skill_id: int, status: bool = True):
    try:
        await svc.SkillService.toggle_status(skill_id, status)
        return ApiResponse.success(message="状态已更新")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{skill_id}", summary="删除技能（目录 + 记录）")
@has_permission("workflow:skill:delete")
async def delete_skill(request: Request, skill_id: int):
    try:
        await svc.SkillService.delete(skill_id)
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/{skill_id}/download", summary="下载技能 zip 包")
@has_permission("workflow:skill:download")
async def download_skill(request: Request, skill_id: int):
    try:
        zip_bytes, filename = await svc.SkillService.download_zip(skill_id)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    quoted = urllib.parse.quote(filename)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.put("/{skill_id}/files", summary="编辑技能内文本文件（预览窗保存）")
@has_permission("workflow:skill:editSkill")
async def update_skill_file(request: Request, skill_id: int, body: SkillFileUpdateRequest):
    try:
        await svc.SkillService.update_file(skill_id, body.path, body.content)
        return ApiResponse.success(message="技能文件已保存")
    except ValueError as e:
        return ApiResponse.error(400, str(e))