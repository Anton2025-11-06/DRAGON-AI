# -*- coding: utf-8 -*-
"""检索路由：多模态检索接口与检索侧就绪探测。

对外口径 ``/api/rag/retrieve**``（网关剥前缀后落到 ``/retrieve**``）。

一颗口子服务三类知识库（SPEC §11.2-5）
--------------------------------------
文本 query 走 BM25+向量混合召回；``imageUrl`` 走图搜图（多模态向量）；跨模态的
「文搜图 / 文搜音视频」由同一个接口按知识库类型自动切换召回逻辑——模态路由在
``RagRetrievalService`` 里按 ``kb_type`` 分组完成，调用方不需要知道每个库怎么召回。
鉴权也在这里收口：可访问的 kb_id 白名单由上层算好后强制下推，ES/Neo4j 只做裸查。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_rag.schemas.rag_schema import RetrieveReq
from service.service_rag.services.retrieval_service import RagRetrievalService

router = APIRouter(prefix="/retrieve", tags=["知识库检索"])


@router.post("", summary="多模态检索（文本/图搜图/跨模态，按库类型自动切召回）")
@has_permission("ai:kb:search")
async def search(request: Request, body: RetrieveReq):
    """kbIds 为空 = 覆盖当前用户全部可用库；非空 = 与白名单取交集（越权的库静默剔除）。

    返回体里的 ``took_ms`` 与 ``warnings`` 是排查召回质量的唯一线索（模型选错、阈值过高、
    某个库压根没授权，都只在这两处露出来），页面应原样显示而不是只画 hits。
    """
    data = await RagRetrievalService.search(await get_login_user(request), body)
    return ApiResponse.success(data=data)


@router.post("/rows", summary="平铺召回（只回结果数组，供批量核对与调试）")
@has_permission("ai:kb:search")
async def search_rows(request: Request, body: RetrieveReq):
    """与工作流知识检索节点同一条召回路径：``topK`` 在这里是「总共返回几条」。

    单独开一颗而不是让调用方自己拍平 ``hits``：两条路跑出不同的召回集，
    是知识库最难自证清白的问题（鉴权、分组、融合逻辑与检索页必须完全一致）。
    """
    rows = await RagRetrievalService.search_rows(
        await get_login_user(request),
        kb_ids=body.kb_ids or [],
        query=body.query or "",
        image_url=body.image_url or "",
        top_k=body.top_k or 0,
        score_threshold=body.score_threshold,
        retrieval_mode=body.mode,
        chunk_type=body.chunk_type,
        rerank=body.rerank,
    )
    return ApiResponse.success(data=rows)


@router.get("/health", summary="检索侧存储就绪状态（ES/向量维度/默认参数）")
async def health(request: Request):
    """配置面板与开放 API 用它区分「没数据」与「连不上」——两件事的处置方式完全不同。"""
    return ApiResponse.success(data=await RagRetrievalService.health())
