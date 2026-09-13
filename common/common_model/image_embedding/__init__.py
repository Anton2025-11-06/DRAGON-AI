# -*- coding: utf-8 -*-
"""一级目录：图片向量（image_embedding，不支持 stream）。

当前仅通义 dashscope 提供 multimodal-embedding；智谱无对应端点、openai 协议亦无标准
图片向量端点，故二者不注册 → 动态发现自然反映真实支持（不硬编码）。
"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_IMAGE_EMBEDDING
from common.common_model.base import BaseModel, ModelResult


class ImageEmbeddingBase(BaseModel):
    category = MT_IMAGE_EMBEDDING

    async def aparse(self, result: ModelResult) -> Any:
        vs = result.vectors or []
        return vs[0] if len(vs) == 1 else vs


from . import dashscope_impl  # noqa: E402,F401
