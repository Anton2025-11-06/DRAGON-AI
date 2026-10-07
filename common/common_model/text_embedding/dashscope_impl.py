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
    # 官方同步接口规定：input 为字符串列表时最多 10 条（v3/v4），每条最长 8192 token。
    # 超了都是 400 InvalidParameter，不是慢一点——条数交给基类拆批，长度交给基类削前缀。
    # 拿字符数当 8192 用：中文一字约一 token 时两者等价，英文只会更保守。
    max_batch_size = 10
    max_item_chars = 8192
