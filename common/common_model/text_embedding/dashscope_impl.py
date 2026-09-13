# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：/compatible-mode/v1/embeddings（text-embedding-v3，1024 维）。"""
from common.common_constants.model_constant import (MT_TEXT_EMBEDDING,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import register
from common.common_model.text_embedding import TextEmbeddingBase


@register(MT_TEXT_EMBEDDING, PROVIDER_DASHSCOPE)
class DashscopeTextEmbedding(TextEmbeddingBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "text-embedding-v3"
