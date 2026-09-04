from fastapi import APIRouter, File, Query, Request, UploadFile

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user
from service.service_rag.schemas.kb_schema import (KbCreateRequest, KbUpdateRequest,
                                                   SearchRequest, ShareRequest)
from service.service_rag.services.kb_service import KbService
from service.service_rag.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/kb", tags=["知识库"])


# ---------------- 检索 ----------------
@router.post("/search", summary="知识库检索(关键词+向量+RRF+Rerank)")
async def search(request: SearchRequest, req: Request):
    user = await get_login_user(req)
    result = await RetrievalService.search(request, user["user_id"])
    return ApiResponse.success(data=result)


# ---------------- 知识库 ----------------
@router.post("", summary="创建知识库")
async def create_kb(request: KbCreateRequest, req: Request):
    user = await get_login_user(req)
    kb = await KbService.create_kb(request, user["user_id"])
    return ApiResponse.success(data={"kb_id": kb.kb_id, "kb_name": kb.kb_name})


@router.get("", summary="知识库列表(我的+公开+共享)")
async def list_kbs(req: Request,
                   page: int = Query(1, ge=1, description="页码"),
                   page_size: int = Query(10, ge=1, le=100, description="每页数量")):
    user = await get_login_user(req)
    result = await KbService.list_kbs(page, page_size, user["user_id"])
    return ApiResponse.success(data=result)


@router.get("/{kb_id}", summary="知识库详情")
async def get_kb(kb_id: int, req: Request):
    user = await get_login_user(req)
    kb = await KbService.get_kb(kb_id, user["user_id"])
    if not kb:
        return ApiResponse.error(code=404, message="知识库不存在")
    return ApiResponse.success(data=kb)


@router.put("/{kb_id}", summary="更新知识库")
async def update_kb(kb_id: int, request: KbUpdateRequest, req: Request):
    user = await get_login_user(req)
    await KbService.update_kb(kb_id, request, user["user_id"])
    return ApiResponse.success("更新成功")


@router.delete("/{kb_id}", summary="删除知识库(含向量)")
async def delete_kb(kb_id: int, req: Request):
    user = await get_login_user(req)
    await KbService.delete_kb(kb_id, user["user_id"])
    return ApiResponse.success("删除成功")


# ---------------- 文档 ----------------
@router.get("/{kb_id}/documents", summary="文档列表")
async def list_docs(kb_id: int, req: Request,
                    page: int = Query(1, ge=1),
                    page_size: int = Query(10, ge=1, le=100)):
    user = await get_login_user(req)
    result = await KbService.list_docs(kb_id, page, page_size, user["user_id"])
    return ApiResponse.success(data=result)


@router.post("/{kb_id}/documents/upload", summary="上传文档(异步向量化入库)")
async def upload_document(kb_id: int, req: Request, file: UploadFile = File(...)):
    user = await get_login_user(req)
    doc_id = await KbService.upload_document(kb_id, user["user_id"], file)
    return ApiResponse.success(data={"doc_id": doc_id}, message="上传成功，向量化任务已投递")


@router.delete("/{kb_id}/documents/{doc_id}", summary="删除文档(含向量)")
async def delete_document(kb_id: int, doc_id: int, req: Request):
    user = await get_login_user(req)
    await KbService.delete_document(kb_id, doc_id, user["user_id"])
    return ApiResponse.success("删除成功")


# ---------------- 授权共享 ----------------
@router.post("/{kb_id}/shares", summary="授权共享(用户/角色)")
async def share_kb(kb_id: int, request: ShareRequest, req: Request):
    user = await get_login_user(req)
    await KbService.share_kb(kb_id, user["user_id"], request.share_type,
                             request.target_id, request.expire_time)
    return ApiResponse.success("授权成功")


@router.delete("/{kb_id}/shares/{share_id}", summary="撤销授权")
async def revoke_share(kb_id: int, share_id: int, req: Request):
    user = await get_login_user(req)
    await KbService.revoke_share(kb_id, share_id, user["user_id"])
    return ApiResponse.success("撤销成功")


@router.get("/{kb_id}/shares", summary="授权列表")
async def list_shares(kb_id: int, req: Request):
    user = await get_login_user(req)
    result = await KbService.list_shares(kb_id, user["user_id"])
    return ApiResponse.success(data=result)