# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：/v4/embeddings（embedding-3，2048 维）。"""
from common.common_constants.model_constant import (MT_TEXT_EMBEDDING,
                                                    PROVIDER_ZHIPU)
from common.common_model.base import register
from common.common_model.text_embedding import TextEmbeddingBase


@register(MT_TEXT_EMBEDDING, PROVIDER_ZHIPU)
class ZhipuTextEmbedding(TextEmbeddingBase):
    provider = PROVIDER_ZHIPU
    default_model = "embedding-3"
