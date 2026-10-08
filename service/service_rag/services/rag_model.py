# -*- coding: utf-8 -*-
"""知识库模型层：模型配置加载 + 建库校验 + 各能力类型的调用门面。

与 common_model 的分工
----------------------
- 调用协议、端点拼装、连接池复用全部在 ``common.common_model``（12 类能力 × 各供应商），
  本模块**不写一行 httpx**，只做知识库业务需要的三件事：
  1. 按 model_id 拿到 ``ModelConfig``（tb_model 是权威源，进程内短 TTL 缓存）；
  2. 建库/改库时校验「这个模型能不能当向量模型/重排模型/问答模型」（SPEC §6/§8/§9）；
  3. 把批量向量化的分批并发、空文本、维度不匹配这类业务规则收敛在一处，
     解析与检索两条链路吃同一个门面，不可能跑出两种向量。

为什么要自持一份加载器（工作流模块已有一份 ModelConfigProvider）
--------------------------------------------------------------
那份的形态是「Redis 路由缓存 + 回源补 api_key」，为的是网关高频转发的读放大；
知识库侧只在解析与检索时按库读三五个模型，量级完全不同。这里直接以 MySQL 为源、
进程内短 TTL 缓存（api_key 只留在本进程内存，不进共享 Redis），少一层缓存一致性问题，
rag worker 里也不需要额外的装配步骤。缓存靠 TTL 自然过期：模型改了参数最坏几十秒后生效，
比在模型管理侧加一层广播（跨服务通知）便宜得多，也不会出现漏广播导致的长期陈旧。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import replace
from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_constants.model_constant import (
    MODEL_TYPE_LABELS, MT_AUDIO_TO_TEXT, MT_IMAGE_UNDERSTAND,
    MT_MULTIMODAL_EMBEDDING, MT_OCR, MT_TEXT_EMBEDDING, MT_TEXT_RERANK,
    MT_TEXT_TO_TEXT, MT_VIDEO_UNDERSTAND,
)
from common.common_log.log_init import log
from common.common_model.model_types import ModelConfig

# 进程内模型配置缓存：{model_id: (过期时间, 配置或 None)}
_MODEL_CACHE: dict[int, tuple[float, Optional[ModelConfig]]] = {}
_MODEL_CACHE_TTL = 30.0

# 走 OpenAI chat 协议、能按对话形式调用的能力类型（问答模型可以是这几类之一）
_CHAT_CAPABLE = {MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR}
# 能直接吃图片/视频输入的理解类型（换能力名复用同一条模型登记即可，厂商端点是同一个）
_VISION_CATEGORIES = {MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR}
# 向量能力类型 → 调用时送文本的入参名（text_embedding 用 input，多模态用 texts）
_EMBED_INPUT_KWARG = {MT_TEXT_EMBEDDING: "input", MT_MULTIMODAL_EMBEDDING: "texts"}


def _label(category: str) -> str:
    return MODEL_TYPE_LABELS.get(category, category or "未知类型")


# ==================== 配置加载 ====================

async def _load_row(model_id: int) -> Optional[ModelConfig]:
    """tb_model 单行 → ModelConfig（不存在返回 None；不校验状态，交给调用方判）。"""
    if not model_id or int(model_id) <= 0:
        return None
    from sqlalchemy import select

    from common.common_mysql.mysql import mysql_client
    # 延迟导入：知识库服务不该在 import 期就把系统服务的实体拉进来（建表元数据全局共享，
    # 提前 import 会牵连出 rbac 一整套表定义）
    from service.service_system.models.model import Model

    rid = int(model_id)
    try:
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                select(Model).where(Model.id == rid))).scalar_one_or_none()
    except Exception as e:  # noqa: BLE001
        log.error("读取模型配置失败 model_id={}: {}", rid, e)
        return None
    if row is None:
        return None
    return ModelConfig(
        model_id=row.id, name=row.name, category=row.category, provider=row.provider,
        model_name=row.model_name, base_url=row.base_url or "", api_key=row.api_key or "",
        model_params=row.model_params or {}, status=row.status,
        supports_stream=bool(row.supports_stream),
        supports_thinking=bool(row.supports_thinking),
        supports_function_call=bool(row.supports_function_call),
    )


class RagModelService:
    """知识库模型门面（全静态方法，与 kb_service 的调用口径一致）"""

    # ==================== 取配置 ====================

    @staticmethod
    async def get_config(model_id: Optional[int], *, force: bool = False
                         ) -> Optional[ModelConfig]:
        """按 id 取模型配置（带进程内 TTL 缓存）；不存在或已停用返回 None。

        停用也返回 None：页面下拉与解析链路都不该拿着一个被管理员关掉的品牌模型继续跑，
        而「模型已停用」与「模型不存在」对用户而言是同一个结果——配置里换一个。
        """
        if not model_id or int(model_id) <= 0:
            return None
        mid = int(model_id)
        if not force:
            hit = _MODEL_CACHE.get(mid)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        config = await _load_row(mid)
        if config is not None and config.status != 1:
            log.warning("模型已停用，按不存在处理: model_id={} ({})", mid, config.name)
            config = None
        _MODEL_CACHE[mid] = (time.monotonic() + _MODEL_CACHE_TTL, config)
        return config

    @staticmethod
    async def require_config(model_id: Optional[int], label: str = "模型") -> ModelConfig:
        """必须拿到可用配置，否则抛可读错误（建库校验与实际调用都走它）。"""
        if not model_id or int(model_id) <= 0:
            raise ValueError(f"未选择{label}")
        config = await RagModelService.get_config(model_id)
        if config is None:
            raise ValueError(f"{label}不存在或已停用（model_id={model_id}），"
                             f"请在模型广场确认可用后重新保存知识库配置")
        return config

    @staticmethod
    def forget_cache(model_id: Optional[int] = None) -> None:
        """清进程内缓存（模型管理改了 key/参数后手工触发一次立即生效用）。"""
        if model_id:
            _MODEL_CACHE.pop(int(model_id), None)
        else:
            _MODEL_CACHE.clear()

    # ==================== 能力判定 ====================

    @staticmethod
    def supports(category: str, config: ModelConfig) -> bool:
        """该模型登记的供应商是否实现了这个能力类型（动态发现，不硬编码支持矩阵）。"""
        from common.common_model import providers_for

        return bool(config and config.provider in providers_for(category))

    @staticmethod
    def _ensure_supported(category: str, config: ModelConfig, label: str) -> None:
        from common.common_model import providers_for

        if RagModelService.supports(category, config):
            return
        avail = providers_for(category)
        raise ValueError(
            f"{label}「{config.name}」的供应商 {config.provider or '未登记'} 不支持"
            f"{_label(category)}（该类型当前支持：{'/'.join(avail) or '无'}）")

    @staticmethod
    def embedding_dim(config: Optional[ModelConfig]) -> int:
        """向量模型的输出维度：取登记参数，未登记按 ES 索引的固定维度。

        维度只有两个可信来源——模型管理里显式登记的维度参数，或厂商端点的固有输出。
        登记值就是生效值：model_params 会被基类原样透传给厂商（见 common_model.base
        的 ``extra``），所以这里漏读一个键名，就等于把一个 768 维的模型当成 1024 维
        放行——建库当场不报错，第一篇文档解析时才被 ES 拒写（实测 ES 9.5.3）。
        键名各家不同：OpenAI 兼容 /embeddings 用 dimensions，dashscope 多模态用
        dimension，两种都要认（漏掉单数写法时，多模态模型会静默回退到索引维度）。
        本项目的 ES 索引建好即固定 ``RAG_VECTOR_DIM``，所以未登记时按它回报，
        与索引保持一致；真要出现 2048 维的向量模型，得先改索引 mapping，
        那时这里也必须一起改（两处不一致的表现是写入报 dimension mismatch）。
        """
        params = dict(getattr(config, "model_params", None) or {})
        for key in ("dimensions", "dimension", "dim", "output_dimension", "vector_dim"):
            value = params.get(key)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return RC.RAG_VECTOR_DIM

    # ==================== 大模型能力判据 ====================

    @staticmethod
    def _ensure_extract_capable(config: ModelConfig) -> None:
        """图谱抽取模型：必须能按对话调用，且该供应商真的实现了 text_to_text。

        实体与关系抽取是按 text_to_text 发出去的（提示词里要 JSON，不需要图片输入），
        所以哪怕登记的是一条多模态模型，最终也要落到 text_to_text 这条实现上；
        供应商没实现该类型时在这里就拒掉，别等构建图谱跑到一半才失败。
        """
        if config.category not in _CHAT_CAPABLE:
            raise ValueError(
                f"图谱抽取模型「{config.name}」是{_label(config.category)}，必须是能按对话调用的"
                f"类型（{'/'.join(_label(c) for c in sorted(_CHAT_CAPABLE))}）")
        if not RagModelService.supports(MT_TEXT_TO_TEXT, config):
            raise ValueError(
                f"图谱抽取模型「{config.name}」的供应商 {config.provider} 不支持实体抽取所需的"
                f"{_label(MT_TEXT_TO_TEXT)}调用，请改选模型或关闭知识图谱")

    @staticmethod
    def _ensure_image_capable(config: ModelConfig) -> None:
        """图片理解模型：登记类型要能吃图片/视频输入，且该供应商真的实现了 image_understand。

        登记成 video_understand / ocr 的模型也算数（qwen-vl 这类厂商本来就是同一个端点），
        调用前由 vision_config 换成 image_understand 的形态；换不出来说明这家没实现，
        建库时就拒——总比解析到每一页图片时静默跳过、最后拿回一堆占位符好。
        """
        if config.category not in _VISION_CATEGORIES:
            raise ValueError(
                f"图片理解模型「{config.name}」是{_label(config.category)}，必须是 "
                f"{'/'.join(_label(c) for c in sorted(_VISION_CATEGORIES))} 之一")
        if RagModelService.vision_config(config, MT_IMAGE_UNDERSTAND) is None:
            raise ValueError(
                f"图片理解模型「{config.name}」的供应商 {config.provider} 不支持"
                f"{_label(MT_IMAGE_UNDERSTAND)}调用，请改选模型或关闭图片智能解析")

    @staticmethod
    def _ensure_category(category: str, config: ModelConfig, label: str) -> None:
        """登记的模型必须是这个能力类型，且该供应商真的实现了它（两种错法一起拒）。

        建库时不拦住的后果是「传了一个音频文件才报解析不了」，而那时文件已落存储、
        任务已排队；ASR 与视频理解都只能读自己的端点，拿文本模型去转写不存在降级一说。
        """
        if config.category != category:
            raise ValueError(f"{label}「{config.name}」是{_label(config.category)}，"
                             f"必须是{_label(category)}")
        RagModelService._ensure_supported(category, config, label)

    # ==================== 建库/改库校验 ====================

    @staticmethod
    async def validate_kb_models(kb_type: str, *, embed_model_id: int,
                                 rerank_model_id: int = 0, chat_model_id: int = 0,
                                 extract_model_id: int = 0, image_model_id: int = 0,
                                 audio_model_id: int = 0, video_model_id: int = 0,
                                 enable_graph: bool = False,
                                 image_understand: bool = False) -> dict[str, Any]:
        """校验一套知识库模型配置，返回 {embed,rerank,chat,extract,image,audio,video,dim}。

        校验点与失败后果一一对应（都必须在建库时拦住，等第一篇文档解析才炸，
        文件已落存储、任务已排队，回滚成本高出几个量级）：
        - 向量模型类型不符 → 写入的向量检索不出来（image 型用文本向量就跨不了模态）；
        - 向量维度不等于索引维度 → ES 直接拒绝写入；
        - 开了图谱却没有可用的抽取模型 → 图谱静默跳过，用户以为功能坏了；
        - 开了图片智能解析却没有图片理解模型 → 文档里的图片全退化成占位符；
        - 音视频库的媒体理解模型看不懂媒体 → SPEC §9 的 media_summary 生成不出来；
        - 文档型库配了音频/视频解析模型但类型或供应商不对 → 传这类文件时才报错，
          所以选了就得在这里验掉（不选不验：纯文本库不该被强制选一个 ASR 模型）。

        doc 型的大模型职责已拆成两位（extract_model_id 图谱抽取、image_model_id 图片理解），
        chat_model_id 只作为存量库的抽取模型回退；audio_video 型仍然只有 chat_model_id 一位。
        """
        kb_type = str(kb_type or "").strip().lower()
        if kb_type not in RC.KB_TYPE_MODEL_REQ:
            raise ValueError(f"不支持的知识库类型：{kb_type}")
        req = RC.KB_TYPE_MODEL_REQ[kb_type]

        # ---- 向量模型：类型 + 供应商 + 维度 ----
        embed = await RagModelService.require_config(embed_model_id, "向量模型")
        allowed = list(req["embed_any"] or [])
        if embed.category not in allowed:
            raise ValueError(
                f"{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}知识库的向量模型必须是 "
                f"{'/'.join(_label(c) for c in allowed)}，"
                f"当前「{embed.name}」是{_label(embed.category)}")
        RagModelService._ensure_supported(embed.category, embed, "向量模型")
        dim = RagModelService.embedding_dim(embed)
        if dim != RC.RAG_VECTOR_DIM:
            raise ValueError(
                f"向量模型「{embed.name}」输出 {dim} 维，与知识库索引固定维度 "
                f"{RC.RAG_VECTOR_DIM} 维不一致；请在模型参数里把维度改为 "
                f"{RC.RAG_VECTOR_DIM}，或改选一个 {RC.RAG_VECTOR_DIM} 维模型")

        # ---- 重排模型：可选，但选了就得是 text_rerank ----
        rerank: Optional[ModelConfig] = None
        if rerank_model_id:
            rerank = await RagModelService.require_config(rerank_model_id, "重排模型")
            if rerank.category != MT_TEXT_RERANK:
                raise ValueError(f"重排模型必须是{_label(MT_TEXT_RERANK)}，"
                                 f"当前「{rerank.name}」是{_label(rerank.category)}")
            RagModelService._ensure_supported(MT_TEXT_RERANK, rerank, "重排模型")

        # ---- 大模型侧：音视频只有「媒体理解」一位；doc 型拆成「图谱抽取」+「图片理解」两位 ----
        understand = req.get("understand_category")
        chat: Optional[ModelConfig] = None
        extract: Optional[ModelConfig] = None
        image: Optional[ModelConfig] = None
        # 音频/视频解析模型只在 doc 型的解析配置里出现（无独立列），非 doc 型恒为 None
        audio: Optional[ModelConfig] = None
        video: Optional[ModelConfig] = None

        if understand:
            # audio_video 型：chat_model_id 就是媒体理解模型，必须能直接读整条媒体
            if chat_model_id:
                chat = await RagModelService.require_config(chat_model_id, "媒体理解模型")
                if chat.category != understand:
                    raise ValueError(
                        f"{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}知识库的媒体理解模型必须是"
                        f"{_label(understand)}（要直接读整条媒体），"
                        f"当前「{chat.name}」是{_label(chat.category)}")
                RagModelService._ensure_supported(understand, chat, "媒体理解模型")
            elif req["chat_required"]:
                raise ValueError(
                    f"{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}知识库需要选择"
                    f"{_label(understand)}模型，用于生成媒体内容摘要")
        else:
            # doc 型：存量库只有 chat_model_id 一列，抽取模型按它回退，
            # 不至于升完级就建不了图谱（新库请在解析配置里直接选抽取模型）
            extract_id = int(extract_model_id or 0) or int(chat_model_id or 0)
            if enable_graph or extract_id:
                extract = await RagModelService.require_config(extract_id, "图谱抽取模型")
                RagModelService._ensure_extract_capable(extract)
            if image_understand and not image_model_id:
                raise ValueError("开启图片智能解析需要选择图片理解模型，"
                                 "否则文档里的图片只会留下占位符")
            if image_model_id:
                image = await RagModelService.require_config(image_model_id, "图片理解模型")
                RagModelService._ensure_image_capable(image)
            if audio_model_id:
                audio = await RagModelService.require_config(audio_model_id, "音频解析模型")
                RagModelService._ensure_category(MT_AUDIO_TO_TEXT, audio, "音频解析模型")
            if video_model_id:
                video = await RagModelService.require_config(video_model_id, "视频解析模型")
                RagModelService._ensure_category(MT_VIDEO_UNDERSTAND, video, "视频解析模型")

        return {"kb_type": kb_type, "embed": embed, "rerank": rerank, "chat": chat,
                "extract": extract, "image": image, "audio": audio, "video": video,
                "dim": dim}

    # ==================== 底层调用 ====================

    @staticmethod
    async def call(category: str, config: ModelConfig, **kwargs) -> Any:
        """按能力类型直连 common_model 并返回 ``ModelResult``。

        错误一律翻译成 ValueError + 一句可执行的提示：模型层抛的是厂商 HTTP 原文，
        直接冒到页面只会是一串 JSON；这里补上「哪个模型、哪个类型」的上下文再交出去。
        """
        from common.common_model import instantiate

        if config is None:
            raise ValueError(f"未配置{_label(category)}模型")
        RagModelService._ensure_supported(category, config, _label(category))
        try:
            inst = instantiate(category, config)
            return await inst.ainvoke(**kwargs)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("模型调用失败: model={}({}) category={} err={}",
                      getattr(config, "name", ""), getattr(config, "model_name", ""),
                      category, e)
            raise ValueError(f"{_label(category)}模型「{getattr(config, 'name', '')}」调用失败："
                             f"{str(e)[:300]}") from e

    # ==================== 向量 ====================

    @staticmethod
    async def embed_texts(config: Optional[ModelConfig], texts: Sequence[str], *,
                          batch_size: Optional[int] = None) -> list[list[float]]:
        """批量文本向量化（返回与入参等长、按序对齐的向量列表）。

        分批 + 信号量并发：批量与并发只取 rag_constant 的 EMBEDDING_BATCH_SIZE / EMBEDDING_CONCURRENCY，
        不在调用点抄数字。等长对齐是硬要求——解析要按 chunk_index 拼 ES 的 _id，
        少一条就会让后面的切片整体错位一位，且错了也看不出来。
        空文本没有可向量化的内容，补零向量占位（调用方据此决定是否跳过写 ES）。
        """
        from service.service_rag.services import rag_settings as settings

        if config is None:
            raise ValueError("未配置向量模型")
        if config.category not in _EMBED_INPUT_KWARG:
            raise ValueError(f"模型「{config.name}」是{_label(config.category)}，"
                             f"不能当向量模型使用")
        category = config.category
        items = list(texts or [])
        if not items:
            return []
        size = int(batch_size or settings.embedding_batch_size())
        size = max(1, size)
        batches = [items[i:i + size] for i in range(0, len(items), size)]

        vectors: list[Optional[list[float]]] = [None] * len(items)
        sem = asyncio.Semaphore(max(1, settings.embedding_concurrency()))

        async def _one(offset: int, batch: list[str]) -> None:
            # 空文本不参与请求（多数厂商会直接 400），但位置要留在结果里
            filled = [(j, t) for j, t in enumerate(batch) if (t or "").strip()]
            if not filled:
                for j in range(len(batch)):
                    vectors[offset + j] = [0.0] * RC.RAG_VECTOR_DIM
                return
            payload = [_clean(t) for _, t in filled]
            async with sem:
                result = await RagModelService.call(
                    category, config, **{_EMBED_INPUT_KWARG[category]: payload})
            got = list(result.vectors or [])
            if len(got) != len(payload):
                raise ValueError(
                    f"向量模型返回 {len(got)} 条，与送出的 {len(payload)} 条不一致，"
                    f"无法按切片序号对齐（请检查模型是否支持批量入参）")
            for (j, _), vec in zip(filled, got):
                vectors[offset + j] = vec
            for j in range(len(batch)):
                if vectors[offset + j] is None:
                    vectors[offset + j] = [0.0] * RC.RAG_VECTOR_DIM

        await asyncio.gather(*[_one(i * size, b) for i, b in enumerate(batches)])
        dims = {len(v or []) for v in vectors}
        if dims != {RC.RAG_VECTOR_DIM}:
            raise ValueError(f"向量维度不符合预期：期望 {RC.RAG_VECTOR_DIM} 维，"
                             f"实际得到 {sorted(dims)} 维（模型登记维度可能与索引不一致）")
        return [v or [] for v in vectors]

    @staticmethod
    async def embed_query(config: Optional[ModelConfig], text: str) -> list[float]:
        """检索用的查询向量（单条文本）。"""
        vecs = await RagModelService.embed_texts(config, [text or ""], batch_size=1)
        return vecs[0] if vecs else [0.0] * RC.RAG_VECTOR_DIM

    @staticmethod
    async def embed_media_text(config: Optional[ModelConfig], text: str) -> list[float]:
        """跨模态检索的文本侧向量（图片/音视频库必须是多模态向量模型）。

        单独一个方法而不是复用 embed_query：图搜库的文本查询若误配了纯文本向量模型，
        这里直接拒绝，而不是把两个不同空间的向量放在一起算相似度。
        """
        if config is None:
            raise ValueError("未配置向量模型")
        if config.category != MT_MULTIMODAL_EMBEDDING:
            raise ValueError(
                f"图片/音视频知识库的检索必须走{_label(MT_MULTIMODAL_EMBEDDING)}，"
                f"当前模型「{config.name}」是{_label(config.category)}")
        result = await RagModelService.call(MT_MULTIMODAL_EMBEDDING, config,
                                            text=_clean(text) or None)
        vecs = result.vectors or []
        if not vecs:
            raise ValueError("多模态向量模型没有返回向量")
        return list(vecs[0])

    @staticmethod
    async def embed_images(config: Optional[ModelConfig], image_urls: Sequence[str],
                           *, batch_size: Optional[int] = None) -> list[list[float]]:
        """图片批量向量化（SPEC §8：一张图一条向量，与文本同空间）。

        这里的 batch_size 只是调用粒度：厂商一次能塞几张由 common_model 的
        contents 配额决定（multimodal-embedding-v1 是图片 1 张/次），超了会在
        模型层拆批，返回仍与送出的图同序。
        """
        from service.service_rag.services import rag_settings as settings

        urls = [u for u in (image_urls or []) if u]
        if not urls:
            return []
        if config is None or config.category != MT_MULTIMODAL_EMBEDDING:
            raise ValueError(f"图片向量化需要{_label(MT_MULTIMODAL_EMBEDDING)}模型")
        size = max(1, int(batch_size or settings.embedding_batch_size()))
        out: list[list[float]] = []
        sem = asyncio.Semaphore(max(1, settings.embedding_concurrency()))

        async def _one(batch: list[str]) -> list[list[float]]:
            async with sem:
                result = await RagModelService.call(MT_MULTIMODAL_EMBEDDING, config,
                                                    image_urls=batch)
            got = list(result.vectors or [])
            if len(got) != len(batch):
                raise ValueError(f"图片向量返回 {len(got)} 条，与送出的 {len(batch)} 张不一致")
            return got

        batches = [urls[i:i + size] for i in range(0, len(urls), size)]
        for part in await asyncio.gather(*[_one(b) for b in batches]):
            out.extend(part)
        return out

    @staticmethod
    async def embed_video(config: Optional[ModelConfig], video_url: str) -> list[float]:
        """整条音视频的全局向量（SPEC §9：一条媒体 = 一条向量）。"""
        if not video_url:
            raise ValueError("缺少媒体地址，无法生成全局向量")
        if config is None or config.category != MT_MULTIMODAL_EMBEDDING:
            raise ValueError(f"音视频向量化需要{_label(MT_MULTIMODAL_EMBEDDING)}模型")
        result = await RagModelService.call(MT_MULTIMODAL_EMBEDDING, config,
                                            video_url=video_url)
        vecs = result.vectors or []
        if not vecs:
            raise ValueError("多模态向量模型没有返回视频向量")
        return list(vecs[0])

    # ==================== 重排 ====================

    @staticmethod
    async def rerank(config: Optional[ModelConfig], query: str, documents: Sequence[str],
                     top_n: int = 5) -> list[dict[str, Any]]:
        """重排，返回 [{'index', 'relevance_score'}]（按分数降序，index 是入参下标）。

        候选不再截断：多少条就送多少条（候选数本身由检索配置的 recall_size 自然界定）。
        厂商 rerank 端点的单次请求上限（通义 gte-rerank-v2 = 500 条 / 30,000 token、
        智谱 rerank = 128 条）由 common_model.text_rerank 拆批兜住，index 仍是
        送出去的这份 documents 的下标，不在这里拿上限剪候选。
        """
        docs = [_clean(d) for d in (documents or []) if (d or "").strip()]
        if not docs or config is None:
            return []
        result = await RagModelService.call(MT_TEXT_RERANK, config, query=_clean(query),
                                            documents=docs,
                                            top_n=max(1, min(int(top_n or 1), len(docs))))
        return list(result.scores or [])

    # ==================== 文本生成与理解 ====================

    @staticmethod
    async def chat(config: Optional[ModelConfig], prompt: str, *,
                   system: Optional[str] = None, **kwargs) -> str:
        """问答/抽取调用（按 text_to_text 发，登记的对话型视觉模型同样走这个端点）。"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": _clean(prompt)})
        result = await RagModelService.call(MT_TEXT_TO_TEXT, config, messages=messages,
                                            **kwargs)
        return (result.content or "").strip()

    @staticmethod
    async def chat_json(config: Optional[ModelConfig], prompt: str, *,
                        system: Optional[str] = None, **kwargs) -> Any:
        """要求模型输出 JSON 的调用（图谱抽取用），带围栏与前后废话的容错解析。

        不做容错的话，模型偶尔在 JSON 外面包一层 ```json 就整篇抽取失败，
        而失败在页面上表现为「图谱构建失败」，用户看不出是自己的提示词问题。
        """
        text = await RagModelService.chat(config, prompt, system=system, **kwargs)
        return _parse_json(text)

    @staticmethod
    def vision_config(config: Optional[ModelConfig], category: str
                      ) -> Optional[ModelConfig]:
        """把库上登记的问答模型换成某个理解能力类型的调用形态；换不出来返回 None。

        只有同为「能吃图片/视频输入」的类型才允许复用同一条登记（qwen-vl-plus 之类
        本来就是 image/video 理解同一个模型）；纯文本模型硬套出一次视觉调用，
        厂商侧报的错用户看不懂，所以这里直接返回 None，由调用方给出「跳过图片」的警告。
        """
        if config is None or category not in _VISION_CATEGORIES:
            return None
        if config.category == category:
            return config
        if config.category not in _VISION_CATEGORIES:
            return None
        clone = replace(config, category=category)
        return clone if RagModelService.supports(category, clone) else None

    @staticmethod
    async def understand_image(config: ModelConfig, image_url: str, prompt: str) -> str:
        """图片描述（SPEC §7.4 图片智能解析的模型侧）。"""
        result = await RagModelService.call(MT_IMAGE_UNDERSTAND, config,
                                            prompt=_clean(prompt), image_url=image_url)
        return (result.content or "").strip()

    @staticmethod
    async def understand_media(config: ModelConfig, media_url: str, prompt: str) -> str:
        """整条音视频的综合描述（SPEC §9-2：不抽帧、不做 ASR，一次输出描述）。"""
        result = await RagModelService.call(MT_VIDEO_UNDERSTAND, config,
                                            prompt=_clean(prompt), video_url=media_url)
        return (result.content or "").strip()

    @staticmethod
    async def transcribe_audio(config: ModelConfig, *, audio_url: str = "",
                               audio_bytes: Optional[bytes] = None,
                               filename: str = "") -> str:
        """整条音频转写成正文（文档型知识库收到音频文件时用它，规约同 understand_media）。

        地址与字节同时递是因为三家的输入形态不同：openai/zhipu 走 multipart 上传
        （直接吃字节，不必要求存储能被公网访问），通义 paraformer 是异步任务、
        只认一个可下载的 URL。两个都传，各家取自己需要的那一个，多余的进 **kwargs 被丢弃。
        """
        kwargs: dict[str, Any] = {"filename": filename or "audio.mp3"}
        if audio_bytes:
            kwargs["audio_bytes"] = audio_bytes
        if audio_url:
            kwargs["audio_url"] = audio_url
        result = await RagModelService.call(MT_AUDIO_TO_TEXT, config, **kwargs)
        return (result.content or "").strip()


# ==================== 内部小工具 ====================

def _clean(text: Any) -> str:
    """送模型前的文本清洗：非字符串兜底 + 去掉首尾空白（多数厂商对空串敏感）。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.strip()


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse_json(text: str) -> Any:
    """模型输出 → Python 结构（剥代码围栏、截取第一段 JSON）。"""
    body = _FENCE.sub("", text or "").strip()
    if not body:
        raise ValueError("模型没有返回内容，无法解析为 JSON")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    # 常见形态是「一句解释 + JSON」：取第一个 { 或 [ 到最后一个 } 或 ] 的区间再试一次
    starts = [i for i, ch in enumerate(body) if ch in "{["]
    for start in starts:
        opener = body[start]
        closer = "}" if opener == "{" else "]"
        end = body.rfind(closer)
        if end <= start:
            continue
        try:
            return json.loads(body[start:end + 1])
        except json.JSONDecodeError:
            continue
    raise ValueError(f"模型返回的内容不是 JSON：{body[:200]}")


__all__ = ["RagModelService"]
