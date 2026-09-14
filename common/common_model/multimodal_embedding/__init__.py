# -*- coding: utf-8 -*-
"""一级目录：多模态向量（multimodal_embedding，不支持 stream）。

把文本 / 图片 / 视频映射到同一语义向量空间（以文搜图、以图搜图、跨模态检索）。

当前仅通义 dashscope 提供 MultiModalEmbedding（multimodal-embedding-v1 等）；智谱开放平台
只有 OpenAI 兼容的纯文本 /embeddings、无多模态向量端点，openai 协议亦无标准多模态向量端点，
故二者不注册 → 动态发现自然反映真实支持（不硬编码）。
"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_MULTIMODAL_EMBEDDING
from common.common_model.base import BaseModel, ModelResult


class MultimodalEmbeddingBase(BaseModel):
    """多模态向量抽象父类：入参支持 text / image / video 任意组合。"""

    category = MT_MULTIMODAL_EMBEDDING

    async def aparse(self, result: ModelResult) -> Any:
        vs = result.vectors or []
        return vs[0] if len(vs) == 1 else vs


from . import dashscope_impl  # noqa: E402,F401
