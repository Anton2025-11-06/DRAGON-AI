# -*- coding: utf-8 -*-
"""知识图谱读取路由：子图可视化、规模统计、实体检索与原文回溯。

对外口径 ``/api/rag/graph/**``（网关剥前缀后落到 ``/graph/**``），权限点 ``ai:kb:graph``
（它挂在「图谱检索」菜单上，与「知识维护」的 ``ai:kb:list`` 分开授权）。
**构建**不在这里——那是投给 graph 流水线的异步任务，跟着知识库走
``POST /api/rag/knowledge-bases/{kb_id}/graph/build``（见 kb_router）。

底层裸查（SPEC §10.3/§13.4）
---------------------------
Neo4j 里没有任何权限概念：每个入口都先过 ``RagGraphService.kb_scope`` 算出白名单
（用户可见的库 ∩ 请求指定的库，并且只留 doc 型——非 doc 型从来没写过实体节点，
把它们带进 Cypher 只会多几个空命中的 id，还会让「图谱为空」与「无权限」混成同一种表现），
再按白名单过滤查询。白名单为空即空结果，绝不退化成全图扫描。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_rag.schemas.rag_schema import GraphQueryReq
from service.service_rag.services.graph_service import RagGraphService

router = APIRouter(prefix="/graph", tags=["知识图谱"])


def _kb_ids(value: Optional[str]) -> Optional[list[int]]:
    """查询串里的 ``kbIds=1,2,3`` → 列表；空串/全非法值按「不限」处理。"""
    if not value:
        return None
    ids = [int(x) for x in value.replace(" ", "").split(",") if x.isdigit()]
    return ids or None


@router.post("/query", summary="子图查询（图谱可视化页）")
@has_permission("ai:kb:graph")
async def query(request: Request, body: Optional[GraphQueryReq] = None):
    """起点优先级：关键词 > 文档限定 > 库内度数最高；拿到起点后按 depth 逐跳展开。

    ``docIds``/``keyword`` 只用来定**起点**，起点之外的邻域仍按知识库展开全部——
    把图硬卡在几篇文档里会让关系链断开，看上去像图谱没数据。
    """
    data = await RagGraphService.query(await get_login_user(request), body or GraphQueryReq())
    return ApiResponse.success(data=data)


@router.get("/statistics", summary="图谱规模概览（知识库详情页）")
@has_permission("ai:kb:graph")
async def statistics(request: Request,
                     kb_ids: Optional[str] = Query(None, alias="kbIds",
                                                   description="逗号分隔的知识库 id")):
    """没图与连不上是两件事，分开回话：``ready=false`` 带 message，空图只回零计数。"""
    return ApiResponse.success(data=await RagGraphService.statistics(
        await get_login_user(request), _kb_ids(kb_ids)))


@router.get("/entities/search", summary="实体检索（图谱页搜索框）")
@has_permission("ai:kb:graph")
async def search_entities(request: Request,
                          keyword: str = Query(..., min_length=1, max_length=100),
                          kb_ids: Optional[str] = Query(None, alias="kbIds"),
                          entity_type: Optional[str] = Query(None, alias="entityType",
                                                             max_length=32),
                          limit: int = Query(0, ge=0, le=1000,
                                             description="0=用配置默认值")):
    data = await RagGraphService.search(await get_login_user(request), keyword,
                                        _kb_ids(kb_ids), entity_type=entity_type, limit=limit)
    return ApiResponse.success(data=data)


@router.get("/entities/sources", summary="实体溯源（点节点回原文切片位置）")
@has_permission("ai:kb:graph")
async def entity_sources(request: Request,
                         name: str = Query(..., min_length=1, max_length=200),
                         kb_ids: Optional[str] = Query(None, alias="kbIds"),
                         entity_type: Optional[str] = Query(None, alias="entityType",
                                                            max_length=32),
                         limit: int = Query(50, ge=0, le=500)):
    data = await RagGraphService.sources(await get_login_user(request), name,
                                         _kb_ids(kb_ids), entity_type=entity_type, limit=limit)
    return ApiResponse.success(data=data)
