# -*- coding: utf-8 -*-
"""rag 流水线的执行体：文档解析（doc 型）、媒体解析（image/audio_video 型）、底层清理。

与 API 侧的分工
----------------
API 进程只入队与读进度，**不在请求线程里解析任何文件**；本模块只在 worker 进程里被调用
（`arq_tasks/tasks/ragflow.py` 的三个任务函数各自驱动一遍）。手工重跑与自动排队走的是
这里的同一个方法，所以两条路径不可能跑出两种结果。

三条流水线的阶段编排（阶段码见 rag_constant.RAG_STAGES，状态见 DOC_STATUS_*）
--------------------------------------------------------------------------
* doc：download → parse → preprocess → chunk → embedding → index（→ graph 另投任务）
  状态机 PENDING → PARSING → ANALYZING → PROCESSING → PROCESSED；
* media：download → embedding → index（没有解析与分块，一条媒体 = 一条切片 = 一条向量）
  状态机 PENDING → ANALYZING → PROCESSING → PROCESSED；
* purge：擦 ES 切片、Neo4j 子图、存储对象（原件 / sidecar / 内嵌图片）。

三条硬口径
----------
1. **可复用的产物绝不重做**：解析一次落 sidecar，改分块配置重跑只吃 sidecar
   （几百页 PDF 走 minerU 一次要几分钟）；判据是 `parse_version` 与库 `version` 相等
   （见 tb_document.parse_version 列注释），手动构建向量可以显式要求复用。
2. **幂等**：ES 的 ``_id = {kb_id}_{doc_id}_{chunk_index}`` + 切片表按 doc_id 物理替换，
   重跑一次不会让切片翻倍；进度与状态都按任务号覆盖。
3. **失败必须留得下原因**：任何异常都翻成 ``FAILED`` + ``error_msg``（页面直接可读），
   而不是把栈喷进日志后让文档卡在中间态。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import delete, select, update

from common.common_constants import rag_constant as RC
from common.common_constants.model_constant import MT_IMAGE_UNDERSTAND
from common.common_es import es_client, to_json
from common.common_es.index_mapping import es_doc_id
from common.common_file_parser import constants as C
from common.common_file_parser.entry import (chunk_parsed_document, parse_document,
                                             sidecar_bytes, sidecar_from_bytes)
from common.common_file_parser.models import ImageRef, ParsedBlock, ParsedDocument
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_neo4j import kg_store, neo4j_client
from common.common_storage import download as storage_download
from common.common_storage import get_storage
from service.service_rag.models.kb_entity import Document, DocumentChunk, KnowledgeBase
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.task_service import RagTaskService

# 一次向量化外层批的大小系数：内层已按 embedding_batch_size 分批并发，
# 外层再分组只为了「能报进度 + 及时把向量写成 ES 释放内存」，不做第二层批量策略
EMBED_GROUP_BATCHES = 4
# 一次 bulk 写 ES 的切片数（与 common_es 的 BULK_SLICE 同量级，够小以免请求体超限）
INDEX_GROUP_SIZE = 500

# 媒体 MIME → 存储对象后缀（文档内嵌媒体落存储时用）。
# 认不全的由 obj_image_name 退到 MIME 子类型（video/mp4 → mp4），最后一档才是 png：
# 内嵌音频/视频不在这里列也会拿到对不上的后缀，存下一个改不回来的对象比没存更难收拾。
_MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp",
    "image/bmp": "bmp", "image/gif": "gif",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac", "audio/flac": "flac",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/x-m4v": "m4v",
    "video/x-msvideo": "avi", "video/x-matroska": "mkv", "video/webm": "webm",
}


class RagParseService:
    """解析与清理的执行体（全静态方法，worker 侧直接 await）"""

    # ==================== 一、doc 型解析 ====================

    @staticmethod
    async def run_parse(doc_id: int, options: Optional[dict] = None) -> dict:
        """一篇文档的完整解析：取原件 → 解析 → 分块 → 向量化 → 写 ES →（开关）自动构建图谱。

        :param options: 任务选项（与入队时的 args 同源）：``{"sidecar": auto/reuse/reparse}``
            控制要不要复用上一轮的解析产物，页面「构建向量」默认走 reuse、「重新解析」走 reparse
        :return: {"doc_id","chunk_count","engine","warnings"}（worker 的返回值只进 arq 结果，
            权威状态仍在 MySQL；返回它是为了让手工调用与单测拿到结果）
        """
        sidecar = _sidecar_mode(options)
        token = await RagTaskService.acquire_doc(doc_id)
        if not token:
            # 同一文档已有任务在跑（重复入队、或上一跑还没释放锁）：跳过比抢跑安全
            log.warning(f"文档已有解析任务在跑，跳过: doc_id={doc_id}")
            return {"doc_id": int(doc_id), "skipped": True}

        kb_id = 0
        try:
            doc, kb = await load_pair(doc_id)
            kb_id = int(kb.kb_id)
            log.info("开始文档解析: doc={} kb={} name={} type={} sidecar={}",
                     doc_id, kb_id, doc.doc_name, kb.kb_type, sidecar)

            # ---------- 阶段 1：取原件 ----------
            await RagTaskService.transition(
                doc_id, RC.DOC_STATUS_PARSING, stage="download",
                message="读取原始文件", reset_progress=True)
            engine, parse_config = settings.normalize_parse_config(
                kb.kb_type, kb.parser_engine, kb.parse_config)
            chunk_config = settings.normalize_chunk_config(kb.chunk_config)
            raw = await _download(doc.file_path)
            # doc 型库也收图片/音频/视频：这类文件抽不出文字，由解析配置里的模型先转写
            media_kind = RC.media_kind_of_ext(doc.file_ext or "")
            log.info("阶段[取原件]完成: doc={} 字节={} 媒体={}",
                     doc_id, len(raw or b""), media_kind or "-")

            # ---------- 阶段 2~3：解析 + 预处理（解析层内部完成） ----------
            await RagTaskService.set_progress(
                doc_id, stage="parse",
                message=(f"{RC.MEDIA_KIND_LABELS.get(media_kind, media_kind)}转写为正文"
                         if media_kind else f"解析引擎 {engine}"))
            # 图片理解模型是公用配置（库上一位，页面常驻）：文档内嵌图片增强与独立图片文件
            # 转写都读它。存量库 image_model_id=0 时回退旧的问答列（历史库都靠它看图）
            image_cfg = await RagModelService.get_config(
                int(kb.image_model_id or 0) or int(kb.chat_model_id or 0))
            # 音频/视频解析模型没有独立列，只存在解析配置里（选了才有值，未选为 0）：
            # 同一位既服务独立媒体文件的转写，也服务文档内嵌音视频的解析增强
            parsed, warnings = await _parse_or_reuse(
                doc, kb, raw, engine=engine, parse_config=parse_config,
                image_cfg=image_cfg,
                audio_cfg=await RagModelService.get_config(
                    int(parse_config.get("audio_model_id") or 0)),
                video_cfg=await RagModelService.get_config(
                    int(parse_config.get("video_model_id") or 0)),
                sidecar=sidecar)
            if parsed.engine != engine and engine == RC.PARSE_ENGINE_AUTO:
                warnings.append(f"自动选择解析引擎：{parsed.engine}")
            log.info("阶段[解析]完成: doc={} 引擎={} 结构块={} 图片块={} 页数={}",
                     doc_id, parsed.engine, len(parsed.blocks or []),
                     parsed.image_count, parsed.page_count)

            # ---------- 阶段 4：分块 ----------
            await RagTaskService.transition(
                doc_id, RC.DOC_STATUS_ANALYZING, stage="chunk", message="按配置切分切片")
            embed_cfg = await RagModelService.require_config(
                kb.embedding_model_id, "向量模型")
            chunks = await chunk_parsed_document(
                parsed, chunk_config, parse_config=parse_config,
                embed_fn=_embed_fn(embed_cfg))
            if not chunks:
                raise ValueError("分块结果为空：文档没有可检索正文（换解析引擎或分块配置后重试）")
            rows = await _replace_chunks(kb, doc_id, chunks, parsed)
            await RagTaskService.set_progress(doc_id, stage="chunk",
                                              total=len(rows), done=len(rows),
                                              message=f"切出 {len(rows)} 个切片")
            log.info("阶段[分块]完成: doc={} 切片={}", doc_id, len(rows))

            # ---------- 阶段 5~6：向量化 + 写索引 ----------
            await RagTaskService.transition(
                doc_id, RC.DOC_STATUS_PROCESSING, stage="embedding",
                total=len(rows), done=0, message="向量化并写入检索索引")
            indexed, skipped = await _embed_and_index(kb, doc_id, embed_cfg, rows)
            if not indexed:
                raise ValueError(f"{len(rows)} 个切片全部没有拿到向量，检索侧将查不到这篇文档")

            # ---------- 收尾 ----------
            await RagTaskService.mark_vectorized(doc_id, chunk_count=len(rows))
            await _save_doc_meta(doc_id, parser_engine=parsed.engine,
                                 parse_version=kb.version, page_count=parsed.page_count,
                                 sidecar_path=doc.sidecar_path)
            result = {"doc_id": int(doc_id), "chunk_count": len(rows),
                      "indexed": indexed, "skipped": skipped,
                      "engine": parsed.engine, "sidecar": sidecar, "warnings": warnings}
            await _finish_doc_warnings(doc_id, warnings)

            # 库级开关：解析完成即自动抽实体（SPEC §7.6-1），失败只记日志不影响向量检索
            # 抽取模型与媒体理解模型分列（图谱抽取在「解析配置」页单独选），存量库回退旧列
            if int(kb.graph_enabled or 0) == 1 and (int(kb.extract_model_id or 0)
                                                   or int(kb.chat_model_id or 0)):
                try:
                    await RagTaskService.enqueue_graph(doc_id, kb_type=kb.kb_type)
                    log.info("已投递构建图谱任务: doc={} kb={}", doc_id, kb_id)
                except ValueError as e:
                    log.warning(f"图谱任务入队失败（向量结果保留）doc={doc_id}: {e}")
            log.info("文档解析完成: doc={} kb={} engine={} 切片={} 写入={}",
                     doc_id, kb_id, parsed.engine, len(rows), indexed)
            if skipped:
                log.warning("部分切片正文为空未写索引: doc={} 跳过={}", doc_id, skipped)
            return result
        except Exception as e:  # noqa: BLE001  worker 侧兜底：原因必须落到 error_msg
            log.exception(f"文档解析失败: doc_id={doc_id}")
            await RagTaskService.fail(doc_id, e)
            raise
        finally:
            await RagTaskService.release_doc(doc_id, token)
            if kb_id:
                await _refresh_counters_safe(kb_id)

    # ==================== 二、image / audio_video 型解析 ====================

    @staticmethod
    async def run_media_parse(doc_id: int, options: Optional[dict] = None) -> dict:
        """一条媒体的解析：多模态向量（+ 音视频综合描述）→ 一条 ES 记录。

        ``options`` 与文档解析同位（doc_id, options），但媒体没有解析产物可复用，
        传了也只当没看见——不开这一位参数就得给两类任务写两个不同的签名。

        与 doc 型的三处差别（SPEC §8/§9）：
        - 不解析、不分块：一张图 / 一条音视频就是一条切片一条向量；
        - 图片型不生成描述（SPEC §8-4「不做图片自动描述」），音视频型必须整条理解；
        - 不做知识图谱（非 doc 型的支持列恒为 0，收尾时也就不必判开关）。
        """
        token = await RagTaskService.acquire_doc(doc_id)
        if not token:
            log.warning(f"媒体已有解析任务在跑，跳过: doc_id={doc_id}")
            return {"doc_id": int(doc_id), "skipped": True}

        kb_id = 0
        try:
            doc, kb = await load_pair(doc_id)
            kb_id = int(kb.kb_id)
            if kb.kb_type == RC.KB_TYPE_DOC:
                raise ValueError("文档型知识库请走文档解析流水线")

            await RagTaskService.transition(
                doc_id, RC.DOC_STATUS_ANALYZING, stage="download",
                message="生成媒体向量", reset_progress=True, total=1, done=0)
            # 送模型的是匿名可访问地址，落库与落索引存的都是**对象名**：
            # 预签名 URL 会过期，写进数据就是留下一批半年后打不开的媒体
            media_ref = str(doc.file_path or "").strip()
            media_url = await _public_url(media_ref)
            embed_cfg = await RagModelService.require_config(
                kb.embedding_model_id, "向量模型")

            summary = ""
            warnings: list[str] = []
            chunk_type = RC.CHUNK_TYPE_IMAGE
            if kb.kb_type == RC.KB_TYPE_IMAGE:
                vectors = await RagModelService.embed_images(embed_cfg, [media_url])
                if not vectors:
                    raise ValueError("多模态向量模型没有返回图片向量")
                vector = list(vectors[0])
                summary = (doc.doc_name or "").strip()
            else:
                chunk_type = RC.CHUNK_TYPE_AUDIO_VIDEO
                chat_cfg = await RagModelService.get_config(kb.chat_model_id)
                if chat_cfg is None:
                    raise ValueError("音视频知识库需要配置视频理解模型（用于生成综合描述）")
                await RagTaskService.set_progress(
                    doc_id, stage="preprocess", message="生成整条媒体的综合描述")
                summary = await RagModelService.understand_media(
                    chat_cfg, media_url, settings.media_summary_prompt())
                if not summary.strip():
                    warnings.append("媒体理解模型没有返回描述，该条媒体只能按向量召回、无摘要文本")
                await RagTaskService.set_progress(
                    doc_id, stage="embedding", message="生成整条媒体的全局向量")
                vector = await RagModelService.embed_video(embed_cfg, media_url)

            # 媒体只有一条记录：写它才能被切片管理页停用/放开（旧行在写入前先物理替换）
            row_id = await _insert_media_chunk(kb, doc, chunk_type, summary, media_ref)
            await RagTaskService.transition(
                doc_id, RC.DOC_STATUS_PROCESSING, stage="index",
                total=1, done=0, message="写入检索索引")
            written = await _index_rows(kb, doc_id, [{
                "chunk_id": row_id, "chunk_index": 0, "chunk_type": chunk_type,
                "content": summary, "media_url": media_ref, "title_path": "",
                "page_num": 0, "sheet_name": "", "available": 1,
                "extra": {"media_type": kb.kb_type, "file_ext": doc.file_ext},
            }], {0: vector})
            # 媒体只写一条，写完立刻刷盘：不刷就要等 ES 自己的 refresh_interval（默认 1s），
            # 这段时间里页面显示「已完成」而检索召不回这条媒体（doc 型在 _embed_and_index 末尾刷）
            if written:
                await _refresh_index_safely(f"媒体切片 doc={doc_id}")
            await RagTaskService.mark_vectorized(doc_id, chunk_count=1)
            await _save_doc_meta(doc_id, media_summary=summary or None)
            await _finish_doc_warnings(doc_id, warnings)
            log.info("媒体解析完成: doc={} kb={} type={} 摘要={} 写入={}",
                     doc_id, kb_id, chunk_type, bool(summary), written)
            return {"doc_id": int(doc_id), "chunk_count": 1,
                    "indexed": written, "warnings": warnings}
        except Exception as e:  # noqa: BLE001
            log.exception(f"媒体解析失败: doc_id={doc_id}")
            await RagTaskService.fail(doc_id, e)
            raise
        finally:
            await RagTaskService.release_doc(doc_id, token)
            if kb_id:
                await _refresh_counters_safe(kb_id)

    # ==================== 三、底层清理 ====================

    @staticmethod
    async def run_purge(kb_id: int, doc_ids: Optional[Sequence[int]] = None) -> dict:
        """删库/删文档后的裸查存储清理：ES 切片 + Neo4j 子图 + 存储对象（顺带清掉图谱抽取断点）。

        只碰存储层，不碰 MySQL（ragflow 任务注释的口径：业务行在 API 进程里已软删）。
        但**读** MySQL：需要按文档行拿到 file_path / sidecar_path 与内嵌图片的对象名，
        存储后端没有列目录能力，对象名只能从业务行反推。

        :param doc_ids: 给定则只清这些文档（删文档动作）；为空则清整个知识库
        :return: {"es","graph","objects","missing"} 三类计数
        """
        ids = [int(d) for d in (doc_ids or []) if d]
        token = await RagTaskService.acquire_kb(kb_id)
        if not token:
            log.warning(f"知识库清理任务已在跑，跳过: kb_id={kb_id}")
            return {"kb_id": int(kb_id), "skipped": True}

        es_count = graph_count = obj_count = missing = 0
        errors: list[str] = []
        try:
            objects, doc_rows = await _collect_objects(kb_id, ids)
            # ---------- ES ----------
            try:
                if ids:
                    for one in doc_rows:
                        es_count += await es_client.delete_by_doc(one, kb_id)
                else:
                    es_count = await es_client.delete_by_kb(kb_id)
            except Exception as e:  # noqa: BLE001
                errors.append(f"ES 清理失败: {str(e)[:200]}")
                log.exception(f"ES purge failed kb={kb_id}")

            # ---------- Neo4j（只有 doc 型写过图谱，非 doc 型这里也是空操作） ----------
            if neo4j_client.ready:
                try:
                    if ids:
                        for one in doc_rows:
                            res = await kg_store.delete_by_doc(kb_id, one)
                            graph_count += int(res.get("deleted_nodes") or 0)
                    else:
                        res = await kg_store.delete_by_kb(kb_id)
                        graph_count += int(res.get("deleted_nodes") or 0)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"图谱清理失败: {str(e)[:200]}")
                    log.exception(f"neo4j purge failed kb={kb_id}")
            else:
                log.info("Neo4j 未初始化，跳过图谱清理（该库本就没有图谱数据）")

            # ---------- 图谱抽取断点（文档都不在了，断点留着就是纯垃圾） ----------
            # drop 内部已把 Redis 异常吞成日志：清理链路不该因为一个旁路键删不掉而报错
            for one in doc_rows:
                await RagTaskService.kg_ckpt_drop(one)

            # ---------- 存储对象 ----------
            backend = get_storage()
            for name in objects:
                try:
                    if await backend.delete(name):
                        obj_count += 1
                    else:
                        missing += 1
                except Exception as e:  # noqa: BLE001
                    missing += 1
                    log.warning(f"存储对象删除失败 {name}: {e}")
            log.info("知识库清理完成: kb={} 指定文档={} ES={} 图节点={} 对象={} 缺失={}",
                     kb_id, len(ids), es_count, graph_count, obj_count, missing)
            if errors:
                raise ValueError("；".join(errors))
            return {"es": es_count, "graph": graph_count, "objects": obj_count,
                    "missing": missing}
        finally:
            await RagTaskService.release_kb(kb_id, token)


# =====================================================================================
# 内部：加载与对象键
# =====================================================================================

async def load_pair(doc_id: int) -> tuple[Document, KnowledgeBase]:
    """取文档行 + 所属知识库行（都不含软删：软删对象的异步任务应当直接失败）。

    图谱构建任务也读这一份（doc 型流水线的公共前置），所以不带下划线对外可见。
    """
    async with mysql_client.get_session() as session:
        doc = (await session.execute(
            select(Document).where(Document.doc_id == int(doc_id),
                                   Document.is_deleted == 0))).scalar_one_or_none()
        if doc is None:
            raise ValueError(f"文档不存在或已删除: doc_id={doc_id}")
        kb = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.kb_id == doc.kb_id,
                                        KnowledgeBase.is_deleted == 0))
            ).scalar_one_or_none()
        if kb is None:
            raise ValueError(f"所属知识库不存在或已删除: kb_id={doc.kb_id}")
        return doc, kb


def obj_raw_name(kb_id: int, doc_id: int, ext: str) -> str:
    """原件对象名：``raw/{kb_id}/{doc_id}.{ext}``（落在 storage.path_prefix=rag/ 之下）。"""
    return f"{RC.RAG_OBJ_DIR_RAW}/{int(kb_id)}/{int(doc_id)}.{(ext or 'bin').strip('.')}"


def obj_sidecar_name(kb_id: int, doc_id: int) -> str:
    """解析产物对象名：``sidecar/{kb_id}/{doc_id}.jsonl``。"""
    return f"{RC.RAG_OBJ_DIR_SIDECAR}/{int(kb_id)}/{int(doc_id)}.jsonl"


def obj_image_name(kb_id: int, doc_id: int, block_id: str, mime: str) -> str:
    """文档内嵌媒体对象名：``image/{kb_id}/{doc_id}/{block_id}.{ext}``（按 block_id 稳定）。

    后缀按 MIME 推：表里没有就取子类型（``video/mp4`` → ``mp4``），两个都不认才退 png。
    目录名仍叫 image/：存量库里已经有几百个对象挂在那儿，为一个目录名去改存储前缀不划算。
    """
    value = str(mime or "").lower()
    ext = _MIME_EXT.get(value) or (value.partition("/")[2].partition(";")[0].strip()
                                   or "png")[:16]
    safe = "".join(ch for ch in str(block_id or "img") if ch.isalnum() or ch in "-_")[:64]
    return f"{RC.RAG_OBJ_DIR_IMAGE}/{int(kb_id)}/{int(doc_id)}/{safe or 'img'}.{ext}"


async def _download(name: str) -> bytes:
    """按对象名读字节（走 common_storage 的统一入口，本地/OSS 由配置决定）。"""
    if not name:
        raise ValueError("文档缺少原件路径，无法解析")
    try:
        return await storage_download(name)
    except ValueError as e:
        raise ValueError(f"原始文件读不到（可能已被清理）：{e}") from e


async def _save_bytes(name: str, data: bytes, content_type: Optional[str] = None) -> None:
    """按**确定性对象名**写字节（解析产物必须能按 doc_id 反推，不能用随机 uuid 名）。"""
    await get_storage().save(name, data, content_type)


async def _public_url(name: str) -> str:
    """对象名 → 模型侧可访问的匿名 URL。

    媒体理解与多模态向量都要求厂商端点能自己拉到文件：OSS 给预签名地址；
    本地后端必须有 storage.public_base，否则拿不到 URL（这里给出可执行的提示）。
    """
    if not name:
        raise ValueError("文档缺少存储路径")
    try:
        url, _expires = await get_storage().public_url(name)
    except ValueError as e:
        raise ValueError(f"取不到匿名访问地址：{e}（本地后端需配置 storage.public_base，"
                         f"或改用 OSS 后端）") from e
    return url


# =====================================================================================
# 内部：解析与分块
# =====================================================================================

def _sidecar_mode(options: Optional[dict]) -> str:
    """从任务选项里取解析产物复用策略（缺字段/认错值一律回到 auto）。

    不报错：旧版本入队的任务（只有一个 doc_id 参数）与手写错值的任务，默认行为都应该是
    「按配置版本自己判」，而不是把一篇文档打成失败。
    """
    mode = str((options or {}).get("sidecar") or "").strip().lower()
    return mode if mode in RC.RAG_SIDECAR_MODES else RC.RAG_SIDECAR_AUTO


async def _parse_or_reuse(doc: Document, kb: KnowledgeBase, raw: bytes, *,
                          engine: str, parse_config: dict, image_cfg: Any,
                          audio_cfg: Any = None, video_cfg: Any = None,
                          sidecar: str = RC.RAG_SIDECAR_AUTO
                          ) -> tuple[ParsedDocument, list[str]]:
    """能复用 sidecar 就绝不重解析；返回 (解析结果, 给页面看的降级说明)。

    三档策略（取值来自动作入队时写的 options）：
    * auto：有 sidecar + 引擎一致 + parse_version 与库当前 version 相同才复用——version 是
      kb_service 在解析/分块配置真变了才 +1 的号，所以它同时代表「这套配置的解析结果还算不算数」；
    * reuse：忽略 version 差强制复用（用户已明确只重分块，重解析是几百页 PDF 的几分钟白跑）；
    * reparse：从不复用（换了引擎、改了预处理开关，旧产物的语义已经不算本次的配置结果）。

    doc 型库也收图片/音频/视频（需求：文档型知识库支持媒体文件）。这类文件解析引擎抽不出
    正文，改走 _media_to_parsed：先让模型把它转写成文字，之后的分块/向量化/图谱与普通文档
    完全一致。转写产物同样落 sidecar，所以「只改分块配置」的重跑不会再付一次模型调用。
    """
    warnings: list[str] = []
    reusable = bool(doc.sidecar_path) and str(doc.parser_engine or "") == engine
    if sidecar == RC.RAG_SIDECAR_REPARSE:
        reusable = False
    elif sidecar == RC.RAG_SIDECAR_AUTO:
        reusable = reusable and int(doc.parse_version or 0) == int(kb.version or 0)
    if reusable:
        try:
            cached = sidecar_from_bytes(await _download(doc.sidecar_path))
            warnings.append("复用已有解析产物（只重跑分块与向量化）")
            log.info("阶段[解析]复用已有解析产物: doc={} 引擎={}", doc.doc_id, engine)
            return cached, warnings
        except Exception as e:  # noqa: BLE001  产物坏了就重解析，不算失败
            warnings.append("上一轮的解析产物不可用（格式版本已升级或文件残缺），本次重新解析")
            log.warning(f"解析产物不可用，改为重新解析 doc={doc.doc_id}: {e}")

    kind = RC.media_kind_of_ext(doc.file_ext or "")
    if kind:
        parsed = await _media_to_parsed(
            doc, raw, kind=kind, engine=engine, parse_config=parse_config,
            image_cfg=image_cfg, audio_cfg=audio_cfg, video_cfg=video_cfg,
            warnings=warnings)
        log.info("阶段[转写]完成: doc={} 媒体={} 结构块={}",
                 doc.doc_id, kind, len(parsed.blocks))
    else:
        hook = _make_image_hook(kb, doc.doc_id, parse_config, image_cfg, warnings,
                                audio_cfg=audio_cfg, video_cfg=video_cfg)
        flags = "/".join(RC.MEDIA_KIND_LABELS[k] for k in
                         (RC.MEDIA_KIND_IMAGE, RC.MEDIA_KIND_AUDIO, RC.MEDIA_KIND_VIDEO)
                         if _understand_on(parse_config, k)) or "无"
        log.info("阶段[解析]开始: doc={} 引擎={} 解析增强={}", doc.doc_id, engine, flags)
        parsed = await parse_document(
            raw, doc.file_name or doc.doc_name, engine=engine,
            parse_config=parse_config, ext=doc.file_ext or "", image_hook=hook,
            engine_options={"mineru": settings.mineru_options()})
    if not parsed.blocks:
        raise ValueError("解析结果为空：这份文件没有可提取的结构块（换引擎或检查文件是否损坏）")

    # 解析产物落存储：重跑分块不必再跑一遍引擎（minerU 一篇要几分钟）
    sidecar_name = obj_sidecar_name(kb.kb_id, doc.doc_id)
    try:
        await _save_bytes(sidecar_name, sidecar_bytes(parsed), "application/x-ndjson")
        doc.sidecar_path = sidecar_name
    except Exception as e:  # noqa: BLE001
        # 存不下 sidecar 不影响本篇解析（本次已经在内存里拿到解析结果），只是下次要重解析
        warnings.append(f"解析产物未能落存储（下次重跑将重新解析）：{str(e)[:120]}")
        log.warning(f"sidecar 落存储失败 doc={doc.doc_id}: {e}")
    return parsed, warnings


async def _media_to_parsed(doc: Document, raw: bytes, *, kind: str, engine: str,
                           parse_config: dict, image_cfg: Any, audio_cfg: Any,
                           video_cfg: Any, warnings: list[str]) -> ParsedDocument:
    """图片/音频/视频 → 结构块（doc 型库的媒体分支：先转成文字，后面当普通文档跑）。

    产物只有两类：图片出一个图片块（描述写进 ImageRef.description），音频/视频出一个文本块。
    两条不能错的铁律：
    * engine 一律回写入参的引擎名——sidecar 的复用判据是 doc.parser_engine == engine，
      媒体根本没走引擎，写真实值会让每次重跑都再付一次转写调用；
    * 缺模型时音频/视频直接报错（转不出正文的文档没有任何可检索内容，静默降级只会
      让人以为解析成功而检索永远召不回来）；图片不报错：图片至少还有文件名与图片位置
      可检索，降级成一句页面警告比拒收整个文件好。
    """
    title = (doc.doc_name or doc.file_name or "").strip()
    label = RC.MEDIA_KIND_LABELS.get(kind, kind)
    parsed = ParsedDocument(name=(doc.file_name or title).rsplit("/", 1)[-1],
                            ext=doc.file_ext or "", engine=engine,
                            meta={"media_kind": kind, "pages": 0})
    if title:
        parsed.blocks.append(ParsedBlock(block_id="b1", type=C.BLOCK_TITLE,
                                        text=title, level=1))
    warnings.append(f"{label}文件由大模型转写为正文，未使用解析引擎")

    url = ""
    try:
        url = await _public_url(doc.file_path)
    except Exception as e:  # noqa: BLE001  地址拿不到只影响“模型能不能看到这条媒体”
        warnings.append(f"{label}取不到匿名访问地址：{str(e)[:120]}")

    if kind == RC.MEDIA_KIND_IMAGE:
        # 原件已在存储，不再重复上传一份：object_key 直接用文档自己的对象名
        ext = str(doc.file_ext or "png").strip(".").lower() or "png"
        ref = ImageRef(block_id=f"b{len(parsed.blocks) + 1}", alt=title,
                       mime=doc.content_type or f"image/{ext}",
                       object_key=str(doc.file_path or ""), url=url)
        vision_cfg = RagModelService.vision_config(image_cfg,
                                                   RC.MEDIA_KIND_CATEGORY[kind])
        if vision_cfg is None:
            # 独立图片文档里描述就是正文（不像 PDF 内嵌图那样周围有文字），
            # 所以只要配了能看图的模型就描述，不看 image_understand 开关
            warnings.append("没有可用的图片理解模型，图片只登记位置，检索只能命中文件名")
        elif not url:
            warnings.append("图片没有可访问地址，跳过图片描述")
        else:
            try:
                ref.description = await RagModelService.understand_image(
                    vision_cfg, url, settings.image_desc_prompt(parse_config))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"图片描述失败：{str(e)[:120]}")
                log.warning(f"图片描述失败 doc={doc.doc_id}: {e}")
        parsed.blocks.append(ParsedBlock(block_id=ref.block_id, type=C.BLOCK_IMAGE,
                                        image=ref))
        return _finalize_media(parsed)

    if kind == RC.MEDIA_KIND_AUDIO:
        if audio_cfg is None:
            raise ValueError("解析音频文件需要在知识库的解析配置里选择音频解析模型"
                             "（音频转文字），当前未配置或该模型已停用")
        if not url:
            warnings.append("音频无可用访问地址，只能按字节上传：只认地址的转写模型（如通义 paraformer）会失败")
    elif video_cfg is None:
        raise ValueError("解析视频文件需要在知识库的解析配置里选择视频解析模型"
                         "（视频理解），当前未配置或该模型已停用")
    elif not url:
        raise ValueError("视频理解模型需要一个可访问的视频地址，当前取不到匿名地址"
                         "（本地后端请配置 storage.public_base，或改用 OSS）")

    if kind == RC.MEDIA_KIND_AUDIO:
        text = await RagModelService.transcribe_audio(
            audio_cfg, audio_url=url, audio_bytes=raw,
            filename=doc.file_name or f"audio.{(doc.file_ext or 'mp3').strip('.')}")
    else:
        text = await RagModelService.understand_media(
            video_cfg, url, settings.video_desc_prompt(parse_config))
    if not (text or "").strip():
        raise ValueError(f"{label}转写结果为空：模型没有吐出任何文字（纯音乐、无人说话的视频、"
                         f"文件过长都会这样），换个模型或换一段素材再试")
    parsed.blocks.append(ParsedBlock(block_id=f"b{len(parsed.blocks) + 1}",
                                    type=C.BLOCK_TEXT, text=text.strip()))
    return _finalize_media(parsed)


def _finalize_media(parsed: ParsedDocument) -> ParsedDocument:
    """媒体转写产物的收口：补 meta（与引擎 finalize 同口径，免得页面统计项缺字段）。

    不跑 finalize 是因为它是引擎的方法（要 self.options），而媒体根本没走任何引擎。
    """
    parsed.meta.setdefault("blocks", len(parsed.blocks))
    parsed.meta.setdefault("images", sum(1 for b in parsed.blocks if b.is_media))
    parsed.meta.setdefault("chars", parsed.char_count())
    return parsed


def _understand_on(parse_config: Optional[dict], kind: str) -> bool:
    """这一种媒体的「解析增强」开关是否打开（开关不限解析引擎，也不限上传方式）。

    认错键名一律当关：内嵌媒体调一次模型就是一次真金白银的调用，
    默认必须是「不开就不花」而不是「没说就花」。
    """
    key = RC.MEDIA_KIND_UNDERSTAND_OPTION.get(kind, "")
    return bool((parse_config or {}).get(key)) if key else False


def _make_image_hook(kb: KnowledgeBase, doc_id: int, parse_config: dict,
                     image_cfg: Any, warnings: list[str],
                     audio_cfg: Any = None, video_cfg: Any = None):
    """构造解析层的内嵌媒体钩子：无条件传存储 + 回填地址，（开了对应增强时）再调大模型描述。

    三种媒体各一个开关（图片/音频/视频解析增强），但口径完全一致：
    * 上传与回填地址永远都做——需求要正文里的媒体一律能看到原位地址；
    * 描述只在「这一种媒体的开关开着」时才调模型，模型也按种类各取各的；
    * 开着却没配模型只警告不失败：正文仍带地址，缺的只是那段描述。

    顺序不能颠倒：描述模型的输入是 URL，先有地址才有描述。
    """
    kinds = (RC.MEDIA_KIND_IMAGE, RC.MEDIA_KIND_AUDIO, RC.MEDIA_KIND_VIDEO)
    models: dict[str, Any] = {}
    for kind in kinds:
        if not _understand_on(parse_config, kind):
            models[kind] = None
            continue
        raw_cfg = {RC.MEDIA_KIND_IMAGE: image_cfg, RC.MEDIA_KIND_AUDIO: audio_cfg,
                   RC.MEDIA_KIND_VIDEO: video_cfg}.get(kind)
        if kind == RC.MEDIA_KIND_IMAGE:
            # 图片模型必须真能吃图输入：配成一个纯文本模型时 vision_config 会回 None
            models[kind] = RagModelService.vision_config(raw_cfg, MT_IMAGE_UNDERSTAND)
            if raw_cfg is not None and models[kind] is None:
                warnings.append(f"图片解析增强未生效：图片理解模型「{raw_cfg.name}」不支持图片输入，"
                                f"图片只传存储不生成描述")
        else:
            models[kind] = raw_cfg
        if models[kind] is None:
            label = RC.MEDIA_KIND_LABELS.get(kind, kind)
            warnings.append(f"{label}解析增强已开启但没有可用的{label}解析模型，"
                            f"{label}只传存储不生成描述")
    image_prompt = settings.image_desc_prompt(parse_config)
    video_prompt = settings.video_desc_prompt(parse_config)

    async def _describe(kind: str, cfg: Any, url: str) -> str:
        """按媒体种类送对应的模型端点（三个端点不是一个协议，不能一把梭）。"""
        if kind == RC.MEDIA_KIND_AUDIO:
            return await RagModelService.transcribe_audio(
                cfg, audio_url=url, audio_bytes=None,
                filename=f"{doc_id}_{kind}")
        if kind == RC.MEDIA_KIND_VIDEO:
            return await RagModelService.understand_media(cfg, url, video_prompt)
        return await RagModelService.understand_image(cfg, url, image_prompt)

    async def _hook(ref: ImageRef, _parsed: ParsedDocument) -> None:
        kind = ref.kind
        label = RC.MEDIA_KIND_LABELS.get(kind, kind)
        if ref.data:
            try:
                name = obj_image_name(kb.kb_id, doc_id, ref.block_id, ref.mime)
                await _save_bytes(name, ref.data, ref.mime or "image/png")
                ref.object_key = name
            except Exception as e:  # noqa: BLE001  单个媒体传不上不该拖垮整篇解析
                warnings.append(f"内嵌{label}上传存储失败：{str(e)[:120]}")
                log.warning(f"媒体上传失败 doc={doc_id} block={ref.block_id}: {e}")
                return
        if not ref.object_key and not ref.url:
            return
        if not ref.url:
            try:
                ref.url = await _public_url(ref.object_key)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"内嵌{label}取不到访问地址（描述跳过）：{str(e)[:120]}")
                return
        cfg = models.get(kind)
        if cfg is None:
            return
        try:
            ref.description = await _describe(kind, cfg, ref.url)
        except Exception as e:  # noqa: BLE001
            # 单个媒体失败不该拖垮整篇文档：留一句说明，正文照常带媒体占位与原位地址
            warnings.append(f"{label} {ref.block_id} 解析增强失败：{str(e)[:120]}")
            log.warning(f"{label}描述失败 doc={doc_id} block={ref.block_id}: {e}")

    return _hook


def _embed_fn(embed_cfg: Any):
    """给语义分块策略用的向量函数（策略内部自行成批后 await embed_fn(texts)）。"""
    async def _fn(texts: Sequence[str]) -> list[list[float]]:
        return await RagModelService.embed_texts(embed_cfg, list(texts))

    return _fn


# =====================================================================================
# 内部：切片落库与写索引
# =====================================================================================

async def _replace_chunks(kb: KnowledgeBase, doc_id: int, chunks: Sequence[Any],
                          parsed: Optional[ParsedDocument]) -> list[dict]:
    """按 doc_id 物理替换切片行（无软删列，见 kb_entity 的表注释），返回带 chunk_id 的行字典。

    物理替换而不是增量：重新分块后块数几乎必然变化，增量只能靠「删掉多出来的」拼回去，
    一旦中途失败就出现同一文档两套 chunk_index，ES 的 _id 也跟着错位。
    """
    rows: list[dict] = []
    async with mysql_client.get_session() as session:
        await session.execute(delete(DocumentChunk).where(
            DocumentChunk.doc_id == int(doc_id)))
        for chunk in chunks:
            image = getattr(chunk, "image", None)
            # 自己的存储对象优先；外链图片（markdown 里的 http 图）只有 url，
            # 少这一条就会把图片切成 media_url=NULL 的文本块（取地址处已兼容完整 URL 直接透出）
            media = (getattr(image, "object_key", "") or getattr(image, "url", "") or "")[:500]
            # 模态按媒体自己的 mime 认：内嵌一段音频却登记成 image，
            # 前端就会拿 <img> 去装一个 mp3，页面上只会碎成一张碎图标
            media_kind = getattr(image, "kind", RC.MEDIA_KIND_IMAGE)
            content = (chunk.content or "").strip()
            embed_text = (chunk.embed_text or chunk.content or "").strip()
            if not content and not media:
                continue      # 空正文且无图片引用的块没有任何可检索内容
            row = DocumentChunk(
                kb_id=kb.kb_id, doc_id=int(doc_id), chunk_index=int(chunk.index),
                chunk_type=chunk.chunk_type or RC.CHUNK_TYPE_TEXT,
                content=content or RC.MEDIA_KIND_PLACEHOLDERS.get(
                    media_kind, RC.MEDIA_KIND_PLACEHOLDER_DEFAULT),
                embed_text=embed_text,
                title_path=(chunk.title_path or "")[:500],
                sheet_name=(chunk.sheet or "")[:128], page_num=int(chunk.page or 0),
                block_id=",".join(chunk.block_ids or [])[:64] or None,
                media_url=media or None,
                media_type=(RC.doc_media_type_of_kind(media_kind) if media else None),
                token_count=int(chunk.tokens or 0), available=1, vectorized=0,
                extra={**(chunk.meta or {}), "block_ids": list(chunk.block_ids or []),
                       "page_end": int(chunk.page_end or 0),
                       "image_description": (getattr(image, "description", "") or "")},
                created_by=kb.created_by)
            session.add(row)
            rows.append({"row": row, "chunk": chunk})
        await session.flush()
        out = []
        for item in rows:
            chunk = item["chunk"]
            image = getattr(chunk, "image", None)
            out.append({
                "chunk_id": item["row"].chunk_id,
                "chunk_index": int(chunk.index),
                "chunk_type": chunk.chunk_type or RC.CHUNK_TYPE_TEXT,
                "content": item["row"].content,
                "embed_text": item["row"].embed_text or item["row"].content,
                "title_path": item["row"].title_path or "",
                "sheet_name": item["row"].sheet_name or "",
                "page_num": item["row"].page_num or 0,
                "media_url": item["row"].media_url or "",
                "available": 1,
                "extra": item["row"].extra or {},
                "image_ref": image,
            })
        await session.commit()
    return out


async def _insert_media_chunk(kb: KnowledgeBase, doc: Document, chunk_type: str,
                              content: str, media_url: str) -> int:
    """媒体型文档只有一条切片（chunk_index=0），写它才能被切片管理页停用/放开。"""
    async with mysql_client.get_session() as session:
        await session.execute(delete(DocumentChunk).where(
            DocumentChunk.doc_id == int(doc.doc_id)))
        row = DocumentChunk(
            kb_id=kb.kb_id, doc_id=int(doc.doc_id), chunk_index=0,
            chunk_type=chunk_type, content=(content or doc.doc_name or "[媒体]")[:RC.MAX_CHUNK_CHARS],
            embed_text=content or "",
            title_path="", page_num=0, media_url=media_url,
            media_type=doc.file_ext or chunk_type,
            token_count=len(content or ""), available=1, vectorized=0,
            extra={"file_ext": doc.file_ext, "file_size": int(doc.file_size or 0),
                   "content_type": doc.content_type},
            created_by=kb.created_by)
        session.add(row)
        await session.flush()
        chunk_id = int(row.chunk_id)
        await session.commit()
    return chunk_id


async def _embed_and_index(kb: KnowledgeBase, doc_id: int, embed_cfg: Any,
                           rows: Sequence[dict]) -> tuple[int, int]:
    """分组向量化并写 ES（返回写入数、跳过数）。

    分组的目的不是批量策略（内层 embed_texts 已按配置分批并发），而是：
    1. 每完成一组就回写进度，大文档不至于整段卡在「向量化中」；
    2. 写完一组即释放该组向量，一篇几万块的文档不至于把向量全留在内存里；
    3. 写 ES 逐组用 refresh=false（不每批等一次刷盘），到末尾统一刷一次：
       一篇文档最多一次刷盘，而「状态已是解析完成」与「ES 里搜得到」不会错开。
    """
    group = max(1, settings.embedding_batch_size() * EMBED_GROUP_BATCHES)
    total = len(rows)
    indexed = skipped = 0
    log.info("阶段[向量化]开始: doc={} 待处理切片={} 每组={}", doc_id, total, group)
    for start in range(0, total, group):
        part = list(rows[start:start + group])
        texts = [(r.get("embed_text") or "").strip() for r in part]
        vectors = await RagModelService.embed_texts(embed_cfg, texts)
        written = await _index_rows(kb, doc_id, part, {
            idx: vec for idx, vec in enumerate(vectors)
            if (texts[idx] or part[idx].get("media_url"))})
        indexed += written
        skipped += len(part) - written
        await _mark_chunks_vectorized([r["chunk_id"] for r in part])
        await RagTaskService.set_progress(
            doc_id, stage="embedding", total=total, done=min(total, start + len(part)),
            message=f"已向量化并写入 {indexed} / {total} 个切片")
        log.info("阶段[向量化]进展: doc={} 已写入 {}/{} 本组写入={} 跳过累计={}",
                 doc_id, indexed, total, written, skipped)
    if indexed:
        # 一篇文档最多刷一次：逐组 refresh=false，到末尾统一刷这一次
        await _refresh_index_safely(f"切片 doc={doc_id}")
    log.info("阶段[向量化]完成: doc={} 写入={} 跳过={}", doc_id, indexed, skipped)
    return indexed, skipped


async def _refresh_index_safely(what: str) -> None:
    """写完刷一次盘，让「状态已完成」与「ES 里搜得到」对齐（refresh_interval 默认 1s）。

    刷盘失败不判失败：等 ES 自己的 refresh_interval 一过就能搜到，
    把一篇已解析的文档回成 FAILED 反而更糟。
    """
    try:
        await es_client.refresh_index()
    except Exception as e:  # noqa: BLE001
        log.warning(f"{what} 写入后刷索引失败（等 refresh_interval 兜底）: {e}")


async def _index_rows(kb: KnowledgeBase, doc_id: int, rows: Sequence[dict],
                      vectors: dict[int, Sequence[float]]) -> int:
    """把一组切片写成 ES 文档（_id 幂等覆盖），返回实际写入条数。

    `vectors` 的下标是本次传入 rows 的序号；正文为空且没有媒体引用的块不给向量，
    所以两者都缺的行直接跳过写入（MySQL 行留着，页面仍能看到它、也能人工启用）。

    **写完不刷盘**（逐批 refresh=false）：刷新由调用方在全部写完的那一刻统一做一次
    （见 _embed_and_index 与 run_media_parse），漏了就会留下「状态已完成却搜不到」的窗口。
    """
    docs: list[dict] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for idx, row in enumerate(rows):
        vector = vectors.get(idx)
        if not vector:
            # 没有向量就没有可召回的可能（空正文块）：MySQL 行留着供人工查看与启用
            continue
        if not any(abs(float(x)) > 0.0 for x in vector[:8]):
            continue      # 全零向量是空文本的占位，写进索引只会污染候选集
        docs.append({
            "_id": es_doc_id(kb.kb_id, doc_id, int(row["chunk_index"])),
            RC.ES_FIELD_ORG: int(kb.org_id or 0),
            RC.ES_FIELD_KB: int(kb.kb_id),
            RC.ES_FIELD_DOC: int(doc_id),
            RC.ES_FIELD_CHUNK: int(row.get("chunk_id") or 0),
            RC.ES_FIELD_INDEX: int(row["chunk_index"]),
            RC.ES_FIELD_TYPE: row.get("chunk_type") or RC.CHUNK_TYPE_TEXT,
            RC.ES_FIELD_CONTENT: row.get("content") or "",
            RC.ES_FIELD_EMBED: [float(x) for x in vector],
            RC.ES_FIELD_MEDIA_URL: row.get("media_url") or None,
            RC.ES_FIELD_TITLE_PATH: row.get("title_path") or None,
            RC.ES_FIELD_PAGE: int(row.get("page_num") or 0),
            RC.ES_FIELD_SHEET: row.get("sheet_name") or None,
            RC.ES_FIELD_AVAILABLE: bool(int(row.get("available", 1))),
            RC.ES_FIELD_DELETED: 0,
            RC.ES_FIELD_CREATE_TIME: now,
            RC.ES_FIELD_SOURCE: to_json(row.get("extra") or {}),
        })
    if not docs:
        return 0
    for start in range(0, len(docs), INDEX_GROUP_SIZE):
        await es_client.put_chunks(docs[start:start + INDEX_GROUP_SIZE], refresh="false")
    return len(docs)


async def _mark_chunks_vectorized(chunk_ids: Sequence[int]) -> None:
    """标记这批切片的向量已写入（断点续跑与「切片是否可检索」的判据）。"""
    ids = [int(i) for i in chunk_ids or [] if i]
    if not ids:
        return
    async with mysql_client.get_session() as session:
        await session.execute(update(DocumentChunk).where(
            DocumentChunk.chunk_id.in_(ids)).values(vectorized=1))
        await session.commit()


# =====================================================================================
# 内部：回写与清理辅助
# =====================================================================================

async def _save_doc_meta(doc_id: int, **fields: Any) -> None:
    """回写文档行的非状态列（解析引擎、页数、sidecar、媒体摘要、解析产物版本号）。"""
    values = {k: v for k, v in fields.items() if v is not None}
    if not values:
        return
    async with mysql_client.get_session() as session:
        await session.execute(update(Document).where(
            Document.doc_id == int(doc_id)).values(**values))
        await session.commit()


async def _finish_doc_warnings(doc_id: int, warnings: Sequence[str]) -> None:
    """把降级说明落到进度里（SPEC §13.4：能跑成但打了折扣，必须让用户看得见）。

    只写进度不写 error_msg：文档状态是 PROCESSED，把提示塞进错误列会让页面
    显示成「已完成 + 一条红字」，两种终态互相打脸。
    """
    text = "；".join(str(w) for w in (warnings or []) if w)
    if not text:
        return
    await RagTaskService.set_progress(doc_id, message=f"完成（{text[:300]}）")
    log.info(f"文档解析带降级说明 doc={doc_id}: {text}")


async def _refresh_counters_safe(kb_id: int) -> None:
    """回写库级计数（失败只记日志：计数是派生值，绝不把已成功的解析判成失败）。"""
    try:
        from service.service_rag.services.kb_service import KnowledgeBaseService

        await KnowledgeBaseService.refresh_counters(kb_id)
    except Exception as e:  # noqa: BLE001
        log.warning(f"知识库计数回写失败 kb={kb_id}: {e}")


async def _collect_objects(kb_id: int, doc_ids: Sequence[int]) -> tuple[list[str], list[int]]:
    """列出要删的存储对象名 + 要清 ES/图谱的文档 id（含软删行，对象名只能从行里反推）。"""
    conds = [Document.kb_id == int(kb_id)]
    if doc_ids:
        conds.append(Document.doc_id.in_([int(d) for d in doc_ids]))
    names: list[str] = []
    targets: list[int] = []
    async with mysql_client.get_session() as session:
        rows = (await session.execute(
            select(Document.doc_id, Document.file_path, Document.sidecar_path,
                   Document.file_ext, Document.media_type)
            .where(*conds))).all()
        for doc_id, file_path, sidecar_path, file_ext, media_type in rows:
            targets.append(int(doc_id))
            if file_path:
                names.append(file_path)
            else:
                names.append(obj_raw_name(kb_id, doc_id, file_ext or "bin"))
            if sidecar_path:
                names.append(sidecar_path)
            else:
                names.append(obj_sidecar_name(kb_id, doc_id))
        media_conds = [DocumentChunk.kb_id == int(kb_id), DocumentChunk.media_url.is_not(None)]
        if doc_ids:
            media_conds.append(DocumentChunk.doc_id.in_([int(d) for d in doc_ids]))
        medias = (await session.execute(
            select(DocumentChunk.media_url).where(*media_conds))).scalars().all()
        # 完整 URL（外链图片）不是本存储的对象名，递进去只会被当成删不掉的垃圾报一轮
        names.extend(str(one) for one in medias
                     if one and "://" not in str(one))
    deduped = list(dict.fromkeys(n for n in names if n))
    return deduped, targets


__all__ = ["RagParseService", "load_pair",
           "obj_raw_name", "obj_sidecar_name", "obj_image_name"]

