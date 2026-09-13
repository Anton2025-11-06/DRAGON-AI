# -*- coding: utf-8 -*-
"""一级目录：文本重排（text_rerank，不支持 stream）。

各家 rerank 端点/报文形态不同（非 OpenAI 标准），故基类只约定入参/返回结构，
ainvoke 交由供应商子类各自直连实现（无桥接）。openai 通用客户端不注册此类型。
"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_TEXT_RERANK
from common.common_model.base import BaseModel, ModelResult


class TextRerankBase(BaseModel):
    """文本重排抽象父类。relevance_score 结果统一放 ModelResult.scores。"""

    category = MT_TEXT_RERANK

    async def aparse(self, result: ModelResult) -> Any:
        """解析：返回 [{'index','relevance_score'}] 列表（按分数降序）。"""
        return result.scores or []


from . import dashscope_impl, zhipu_impl  # noqa: E402,F401
