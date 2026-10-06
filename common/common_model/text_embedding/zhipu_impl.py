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
    # 官方规定：embedding-3 的输入数组最大 64 条（单条请求总量还有个 3072 token 上限）。
    # 比它小的调用基类仍是一次一发，只有超限才拆。
    max_batch_size = 64
