# -*- coding: utf-8 -*-
"""供应商 openai：通用 OpenAI 兼容 /embeddings。"""
from common.common_constants.model_constant import (MT_TEXT_EMBEDDING,
                                                    PROVIDER_OPENAI)
from common.common_model.base import register
from common.common_model.text_embedding import TextEmbeddingBase


@register(MT_TEXT_EMBEDDING, PROVIDER_OPENAI)
class OpenAITextEmbedding(TextEmbeddingBase):
    provider = PROVIDER_OPENAI
    default_model = "text-embedding-3-small"
