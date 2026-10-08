# -*- coding: utf-8 -*-
"""知识库管理路由：配置 CRUD、下拉数据源、运行参数回显，以及库内文档/图谱的写入口。

对外口径
--------
``/api/rag/knowledge-bases/**``。网关把 ``/api/{service}/{path}`` 剥成 ``http://实例:端口/{path}``
（见 service_gateway/routers/gateway_router.py），所以本文件只写剥完前缀之后的路径，
不再重复挂 ``/api/rag``。

鉴权两层各管一段（SPEC §3.1）
-----------------------------
- ``@has_permission`` 只管全局入口：列表页准入（``ai:kb:list``）、能新建（``ai:kb:add``），
  权限点与前端按钮显隐同源，见 sql/v2_init.sql PART 8.9；
- 具体到某个库能不能看/改/删、能不能往里传文件，全部由 service 层的 ACL 资源动作判定
  （common_permission/resource_guard 的 knowledge_base / document 两类）。行级按钮再挂一遍
  功能权限就成了「管理员能改别人的库、被授权的人改不动」这类错位。

路由声明顺序：固定路径（/page、/options、/runtime-config）必须先于 /{kb_id}，
否则会被动态段吞掉。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, Query, Request, UploadFile

from common.common_constants import rag_constant as RC
from common.common_entity.response_schema import ApiResponse
from common.common_file_parser.engines import engine_formats
from common.common_file_parser.entry import config_summary
from common.common_permission.permission import get_login_user, has_permission
from common.common_storage import MAX_FILE_SIZE
from service.service_rag.schemas.rag_schema import (
    BuildVectorReq, DocPageReq, GraphBuildReq, IdsReq, KbPageReq, KbSaveReq, RuntimeConfigResp,
)
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.doc_service import RagDocService
from service.service_rag.services.graph_service import RagGraphService
from service.service_rag.services.kb_service import KnowledgeBaseService

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


# ==================== 固定路径（先声明） ====================

@router.post("/page", summary="知识库分页列表")
@has_permission("ai:kb:list")
async def page(request: Request, body: Optional[KbPageReq] = None):
    """按名称/类型过滤，只回当前用户可见的库（归属 ∪ 数据范围 ∪ 显式授权）。"""
    data = await KnowledgeBaseService.page(await get_login_user(request),
                                           body or KbPageReq())
    return ApiResponse.success(data=data)


@router.get("/options", summary="可使用知识库下拉（检索页/工作流知识检索节点）")
async def options(request: Request):
    """口径是 ACL 的 use 而不是 view：能被引用不等于能改配置。"""
    return ApiResponse.success(data=await KnowledgeBaseService.options(
        await get_login_user(request)))


@router.get("/runtime-config", summary="运行参数与能力探测（配置面板据此置灰）")
@has_permission("ai:kb:list")
async def runtime_config(request: Request,
                         kb_type: Optional[str] = Query(None, description="doc/image/audio_video"),
                         ext: str = Query("", max_length=16, description="按文件后缀探测建议策略"),
                         engine: Optional[str] = Query(None, description="指定解析引擎探可用性")):
    """回显的是**归一后的生效值**而不是用户填的原值：越界值被夹到哪儿、独占策略被降级成
    什么，页面上必须看得见（SPEC §13-2「不写死参数」的另一半——前端也不该自己抄一份默认值）。

    这里只给调用协议约束与默认展示规模（批量/并发/图谱默认节点数/开放 API 单次文件数），
    预览截断长度、重排候选上限这类人为数据上限已经从代码里删掉，不再回显。
    """
    summary = config_summary(ext=ext.strip().lstrip(".").lower(), engine=engine)
    snap = settings.runtime_snapshot()
    data = RuntimeConfigResp(
        engines=summary["engines"],
        strategies=summary["strategies"],
        parse_defaults=summary["parse_config"],
        chunk_defaults=summary["chunk_config"],
        retrieve_defaults=dict(RC.RETRIEVE_DEFAULTS),
        vector_dim=snap["vector_dim"],
        max_chunk_chars=snap["max_chunk_chars"],
        mineru_configured=snap["mineru_configured"],
        es_index=snap["es_index"],
        embedding_batch_size=snap["embedding_batch_size"],
        open_api_max_files=snap["open_api_max_files"],
    ).dump()
    # 面板与上传弹窗还需要这些事实，一并回给前端（都是无副作用的探测/常量投影）
    data.update({
        "engineEffective": summary["engine_effective"],
        "engineOptions": list(RC.PARSE_ENGINES_ALL),
        "suggestStrategy": summary["suggest_strategy"],
        "chunkWarnings": summary["chunk_warnings"],
        "titlePathEnabled": summary["title_path_enabled"],
        "kbTypes": [{"value": t, "label": RC.KB_TYPE_LABELS.get(t, t)} for t in RC.KB_TYPES_ALL],
        "allowedExts": {t: list(e) for t, e in RC.KB_TYPE_ALLOWED_EXTS.items()},
        # 各引擎真读得动的文档格式（解析层声明的唯一数据源）：配置页要按它写清
        # native/mineru 的覆盖范围，前端自己抄一份必然与引擎代码漂移
        "engineFormats": {e: engine_formats(e) for e in RC.PARSE_ENGINES_ALL},
        # 媒体后缀按种类分组下发：三个「解析增强」开关的 hint 要说清各自管哪一类文件
        "mediaExts": {RC.MEDIA_KIND_IMAGE: list(RC.RAG_IMAGE_EXTS),
                      RC.MEDIA_KIND_AUDIO: list(RC.RAG_AUDIO_EXTS),
                      RC.MEDIA_KIND_VIDEO: list(RC.RAG_VIDEO_EXTS)},
        "mediaKindLabels": dict(RC.MEDIA_KIND_LABELS),
        "chunkTypes": list(RC.CHUNK_TYPES_ALL),
        "docStatuses": list(RC.DOC_STATUS_ALL),
        "graphEntityTypes": list(RC.KG_ENTITY_TYPES),
        "maxFileSizeMb": MAX_FILE_SIZE // 1024 // 1024,
        "graphVizDefaultLimit": snap["graph_viz_default_limit"],
        "graphExtractBatchChunks": snap["graph_extract_batch_chunks"],
        "embeddingConcurrency": snap["embedding_concurrency"],
        "configLoaded": settings.is_loaded(),
    })
    return ApiResponse.success(data=data)


@router.post("", summary="新建知识库")
@has_permission("ai:kb:add")
async def create(request: Request, body: KbSaveReq):
    kb_id = await KnowledgeBaseService.create(await get_login_user(request), body)
    return ApiResponse.success(data=kb_id, message="创建成功")


# ==================== 单个知识库 ====================

@router.get("/{kb_id}", summary="知识库详情（配置面板回显）")
async def detail(request: Request, kb_id: int):
    return ApiResponse.success(data=await KnowledgeBaseService.detail(
        await get_login_user(request), kb_id))


@router.put("/{kb_id}", summary="修改知识库（类型与向量模型锁定）")
async def modify(request: Request, kb_id: int, body: KbSaveReq):
    ok = await KnowledgeBaseService.modify(await get_login_user(request), kb_id, body)
    return ApiResponse.success(data=ok, message="保存成功")


@router.delete("/{kb_id}", summary="删除知识库（软删 + 异步清 ES/Neo4j/存储）")
async def remove(request: Request, kb_id: int):
    await KnowledgeBaseService.remove(await get_login_user(request), kb_id)
    return ApiResponse.success(message="删除成功，底层切片与实体正在清理")


# ==================== 库内文档（页面上传与批量管理） ====================

@router.post("/{kb_id}/documents/page", summary="文档分页列表")
async def documents_page(request: Request, kb_id: int, body: Optional[DocPageReq] = None):
    """三类知识库共用一个列表口：预览形态由返回的 mediaType 决定（SPEC §11.1-4）。"""
    data = await RagDocService.page(await get_login_user(request), kb_id, body or DocPageReq())
    return ApiResponse.success(data=data)


@router.post("/{kb_id}/documents/upload", summary="上传文档（多文件批量，异步解析）")
async def upload_documents(request: Request, kb_id: int,
                           files: List[UploadFile] = File(...)):
    """页面上传：逐文件回执，成 10 个失败 2 个是常态，不整批回滚。

    落存储的对象名带 kb_id 前缀（SPEC §11.2-1），文件本体不在本请求里解析——
    接口只做「收文件 + 建行 + 入队」，解析/分块/向量化由 rag 流水线的 worker 跑。
    """
    result = await RagDocService.upload(await get_login_user(request), kb_id, files)
    return ApiResponse.success(data=result, message=_upload_message(result))


@router.post("/{kb_id}/documents/delete", summary="批量删除文档（异步清底层）")
async def delete_documents(request: Request, kb_id: int, body: IdsReq):
    data = await RagDocService.remove(await get_login_user(request), kb_id, body.ids)
    return ApiResponse.success(data=data, message=f"已删除 {data.get('accepted', 0)} 个文档")


@router.post("/{kb_id}/documents/build-vectors", summary="构建向量（先删旧向量再重新向量化）")
async def build_vectors(request: Request, kb_id: int, body: BuildVectorReq):
    """页面按钮「构建向量」（原名重新分块）：没勾选文档直接报错，不给「不传=整库」的兜底。

    默认只吃已有解析产物（改分块配置最常见的操作不该重跑几百页 PDF 的解析）；
    ``reparse=true`` 时连解析一起重跑——换了引擎或预处理开关之后的正确姿势。
    """
    data = await RagDocService.build_vectors(await get_login_user(request), kb_id, body)
    return ApiResponse.success(data=data,
                               message=f"已投递 {data.get('accepted', 0)} 个文档的构建向量任务")


# ==================== 库内知识图谱（SPEC §7.6-2 手动触发） ====================

@router.post("/{kb_id}/graph/build", summary="构建图谱（先清旧图数据再重抽）")
async def build_graph(request: Request, kb_id: int, body: GraphBuildReq):
    """页面按钮「构建图谱」：doc_ids 必填（与构建向量同口径，不给整库兜底）；
    构建前会先清掉这些文档上一轮的图数据，重跑不会在图上叠加历史抽取结果。"""
    data = await RagGraphService.build(await get_login_user(request), kb_id, body)
    return ApiResponse.success(
        data=data,
        message=f"已投递 {data.get('accepted', 0)} 个文档的图谱构建"
                + (f"，跳过 {data.get('skipped', 0)} 个" if data.get("skipped") else ""))


def _upload_message(result: dict) -> str:
    """上传回执的人话版本（失败原因已经在 items 里逐条给了，这里只说成几个）。"""
    accepted = int(result.get("accepted") or 0)
    rejected = int(result.get("rejected") or 0)
    if not accepted:
        return "没有任何文件被接收，请查看逐条原因"
    return f"已接收 {accepted} 个文件，解析任务已投递" + (f"，拒绝 {rejected} 个" if rejected else "")
