# -*- coding: utf-8 -*-
"""供应商 openai：通用 OpenAI 兼容 /embeddings。

max_batch_size 取 10：本项目的 openai = 通用兼容客户端，登记表里已实测的标识走的
就是通义兼容端点（见 common_constants.model_registry 的注），那个通路只吃 10 条。
拆批只会多几次请求、不改变结果，比默认不拆然后被厂商 400 好；真接上 OpenAI 官方
（数组上限 2048）想一把发的，把这个类属性改大即可。

max_item_chars 同理跟着通义取 8192：OpenAI 官方对 text-embedding-3 给的单条上限是
8191 token，真接官方时把它改小到 8000 留余量（中文一字可能占两个 token，贴边不安全）。
"""
from common.common_constants.model_constant import (MT_TEXT_EMBEDDING,
                                                    PROVIDER_OPENAI)
from common.common_model.base import register
from common.common_model.text_embedding import TextEmbeddingBase


@register(MT_TEXT_EMBEDDING, PROVIDER_OPENAI)
class OpenAITextEmbedding(TextEmbeddingBase):
    provider = PROVIDER_OPENAI
    default_model = "text-embedding-3-small"
    # 与 dashscope 同值：兼容端点的实际天花板由后端决定，拿已验证通路作缺省
    max_batch_size = 10
    max_item_chars = 8192
