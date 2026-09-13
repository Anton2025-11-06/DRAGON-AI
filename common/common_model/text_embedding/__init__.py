# -*- coding: utf-8 -*-
"""一级目录：文本向量（text_embedding，不支持 stream）。"""
from __future__ import annotations

from typing import Any, Optional, Union

from common.common_constants.model_constant import MT_TEXT_EMBEDDING
from common.common_model.base import BaseModel, ModelResult


class TextEmbeddingBase(BaseModel):
    """文本向量抽象父类。走各家 OpenAI 兼容 /embeddings 端点（经 AsyncOpenAI）。"""

    category = MT_TEXT_EMBEDDING

    async def ainvoke(self, input: Union[str, list] = None, **kwargs) -> ModelResult:
        if input is None:
            raise ValueError("文本向量需提供 input")
        resp = await self.oai().embeddings.create(
            model=self.model, input=input, **{k: v for k, v in {**self.extra, **kwargs}.items() if v is not None})
        vectors = [d.embedding for d in resp.data]
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return ModelResult(vectors=vectors, usage=usage, raw=resp.model_dump())

    async def aparse(self, result: ModelResult) -> Any:
        """解析：单条输入返回一维向量，多条返回二维。"""
        vs = result.vectors or []
        return vs[0] if len(vs) == 1 else vs


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
