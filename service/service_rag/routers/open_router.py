# -*- coding: utf-8 -*-
"""开放 API 路由：给外部脚本/系统用的批量上传与检索口（SPEC §11.2-1）。

对外口径 ``/api/rag/open/**``（网关剥前缀后落到 ``/open/**``）。

与页面接口的差别只有一处
------------------------
**不重复实现一套业务**。上传仍然调 ``RagDocService.upload``、检索仍然调
``RagRetrievalService.search``，只是把 ``open_api=True`` 传下去——那条路会按
``rag_constant.OPEN_API_MAX_FILES`` 卡单次文件数（当前 100，给脚本用的软门槛；页面一次拖
多少个文件属于人在操作，交给前端自己控制更合理）。除此之外本模块不再对数据量做任何限制。

鉴权口径
--------
调用方仍带 ``Authorization``（网关校验后注入 ``X-User-Token``），因此：
- 功能权限点这里**不挂** ``@has_permission``：脚本不必持有菜单权限；
- 数据权限一条不松：能不能往这个库传文件按该库 ACL 的 ``upload`` 动作，
  检索按 ``use`` 动作算 kb_id 白名单（SPEC §3.2「上层鉴权、底层裸查」）；
  越权的库直接从白名单里消失，不会报「查得到但没数据」。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, Query, Request, UploadFile

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user
from service.service_rag.schemas.rag_schema import IdsReq, RetrieveReq
from service.service_rag.services.doc_service import RagDocService
from service.service_rag.services.kb_service import KnowledgeBaseService
from service.service_rag.services.retrieval_service import RagRetrievalService

router = APIRouter(prefix="/open", tags=["知识库开放API"])


@router.get("/knowledge-bases", summary="可使用的知识库清单（客户端选库用）")
async def knowledge_bases(request: Request):
    return ApiResponse.success(data=await KnowledgeBaseService.options(
        await get_login_user(request)))


@router.post("/knowledge-bases/{kb_id}/documents/upload",
             summary="批量上传文档（开放 API，单次最多 100 个文件）")
async def upload_documents(request: Request, kb_id: int,
                           files: List[UploadFile] = File(...)):
    """逐文件回执、不整批回滚：脚本一次投 50 个文件，成 48 个失败 2 个是正常结果，
    整批回滚会让调用方把 50 个全部重投一遍。

    接口返回只代表「文件已收下、任务已入队」，解析进度要另外轮询
    ``GET /api/rag/open/documents/{docId}/progress``。
    """
    result = await RagDocService.upload(await get_login_user(request), kb_id, files,
                                        open_api=True)
    accepted = int(result.get("accepted") or 0)
    rejected = int(result.get("rejected") or 0)
    return ApiResponse.success(
        data=result,
        message=f"已接收 {accepted} 个文件" + (f"，拒绝 {rejected} 个" if rejected else ""))


@router.post("/documents/progress/batch", summary="批量解析进度")
async def progress_batch(request: Request, body: IdsReq):
    return ApiResponse.success(data=await RagDocService.progress_many(
        await get_login_user(request), body.ids))


@router.get("/documents/{doc_id}/progress", summary="解析进度（客户端轮询）")
async def progress(request: Request, doc_id: int):
    return ApiResponse.success(data=await RagDocService.progress(
        await get_login_user(request), doc_id))


@router.post("/retrieve", summary="多模态检索（与检索页同一条召回路径）")
async def retrieve(request: Request, body: RetrieveReq,
                   with_graph: Optional[bool] = Query(
                       None, description="是否附带图谱实体（仅 doc 型生效）")):
    if with_graph is not None:
        body.with_graph = with_graph
    return ApiResponse.success(data=await RagRetrievalService.search(
        await get_login_user(request), body))
