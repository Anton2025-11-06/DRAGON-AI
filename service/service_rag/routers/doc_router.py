# -*- coding: utf-8 -*-
"""文档与切片路由：详情/双维度进度/重试、预览原文件与解析后正文、切片管理/编辑/删除。

对外口径 ``/api/rag/documents/**``（网关剥掉 ``/api/rag`` 后落到 ``/documents/**``）。
上传与整库删除在 kb_router（那些动作的主语是知识库），这里的主语是**单份文档**：
它的预览形态、进度轮询、失败重试、切片正文编辑。

异步动作的边界（SPEC §13-3）
----------------------------
本路由只投任务与读进度，绝不在请求线程里解析文件：
- ``retry`` 落到 arq 的 rag 流水线：解析与向量化一旦开跑就只能跑到收尾，不提供中途撤销；
- 进度取 Redis（worker 每阶段写一次 hash），状态取 MySQL，两者同一份键，不会两份账；
- 唯一的同步重算例外是「改单条切片正文」附带的那一次向量化——一个 HTTP 往返只动一条，
  为它排一次异步任务反而更容易出现「页面已改但索引还是旧的」这种长时间说不清的不一致。

路由声明顺序：``/progress/batch``、``/retry``、``/chunks/{chunk_id}`` 这些固定段
先于 ``/{doc_id}``，读起来也不用赌动态段会不会吞掉它们。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

from common.common_constants import rag_constant as RC
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user
from service.service_rag.schemas.rag_schema import (
    ChunkPageReq, ChunkUpdateReq, DocActionReq, DocTitleReq, IdsReq,
)
from service.service_rag.services.doc_service import RagDocService

router = APIRouter(prefix="/documents", tags=["知识库文档"])


# ==================== 固定路径（先声明） ====================

@router.post("/progress/batch", summary="批量进度（列表页整屏刷一次）")
async def progress_batch(request: Request, body: IdsReq):
    """比每行发一个请求少 N-1 次往返；已被别人删掉的行直接跳过，不顶掉整屏。"""
    data = await RagDocService.progress_many(await get_login_user(request), body.ids)
    return ApiResponse.success(data=data)


@router.post("/retry", summary="失败重试/手动重跑（批量，逐项回执）")
async def retry(request: Request, body: DocActionReq):
    """一律换新任务号投递：沿用旧 job_id 会被 arq 判成重复投递静默跳过。"""
    sidecar = RC.RAG_SIDECAR_REPARSE if body.reparse else RC.RAG_SIDECAR_AUTO
    data = await RagDocService.retry(await get_login_user(request), body.doc_ids,
                                     sidecar=sidecar)
    return ApiResponse.success(data=data,
                               message=f"已重投 {data.get('accepted', 0)} 个文档"
                                       + (f"，失败 {data.get('rejected', 0)} 个"
                                          if data.get("rejected") else ""))


@router.put("/chunks/{chunk_id}", summary="编辑切片（正文/可用性）")
async def update_chunk(request: Request, chunk_id: int, body: ChunkUpdateReq):
    """改正文会连带重算这一片的向量；重算失败照样回话，不留「显示新内容、命中旧语义」。"""
    data = await RagDocService. chunk_update(await get_login_user(request), chunk_id, body)
    return ApiResponse.success(data=data, message="保存成功")


@router.delete("/chunks/{chunk_id}", summary="删除切片（连带清向量与图谱数据）")
async def delete_chunk(request: Request, chunk_id: int):
    """三份一起清：MySQL 元数据、ES 向量、Neo4j 溯源节点（只删一份就是没删干净）。

    底层清理失败不回滚已删的行，而是在 message 里说哪一层没清掉，页面重试一次就好。
    """
    data = await RagDocService.chunk_remove(await get_login_user(request), chunk_id)
    note = data.get("message")
    return ApiResponse.success(data=data, message=(note or "切片已删除"))


# ==================== 单份文档 ====================

@router.get("/{doc_id}", summary="文档详情")
async def detail(request: Request, doc_id: int):
    return ApiResponse.success(data=await RagDocService.detail(
        await get_login_user(request), doc_id))


@router.get("/{doc_id}/progress", summary="解析进度（向量化与图谱两条分段进度）")
async def progress(request: Request, doc_id: int):
    """两个维度各一个 Redis 键，一次轮询全部回齐（需求 11）。"""
    return ApiResponse.success(data=await RagDocService.progress(
        await get_login_user(request), doc_id))


@router.get("/{doc_id}/original", summary="预览原文件（签名地址）")
async def original(request: Request, doc_id: int):
    """卡 ACL 的 preview 动作；回 url + mediaType + contentType，前端据此选打开方式。

    不在列表里直接下原件地址：预览是要鉴权的动作，把对象名拼进返回体等于绕过 ACL。
    """
    return ApiResponse.success(data=await RagDocService.original_preview(
        await get_login_user(request), doc_id))


@router.get("/{doc_id}/content", summary="查看解析后文档（拼接全部切片，不截断）")
async def content(request: Request, doc_id: int):
    """只读 MySQL 的切片表：预览要看的是「我们存下来的原文」，
    而 ES 那份是为检索准备的副本（可能被截断、也可能还没刷盘）。"""
    return ApiResponse.success(data=await RagDocService.content_preview(
        await get_login_user(request), doc_id))


@router.put("/{doc_id}/title", summary="改文档标题")
async def rename(request: Request, doc_id: int, body: DocTitleReq):
    await RagDocService.rename(await get_login_user(request), doc_id, body.title)
    return ApiResponse.success(message="修改成功")


@router.post("/{doc_id}/chunks/page", summary="切片分页列表（正文在这里看）")
async def chunks_page(request: Request, doc_id: int, body: Optional[ChunkPageReq] = None):
    data = await RagDocService.chunks_page(await get_login_user(request), doc_id,
                                           body or ChunkPageReq())
    return ApiResponse.success(data=data)
