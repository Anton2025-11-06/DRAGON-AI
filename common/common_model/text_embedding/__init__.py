# -*- coding: utf-8 -*-
"""一级目录：文本向量（text_embedding，不支持 stream）。"""
from __future__ import annotations

from typing import Any, Optional, Union

from common.common_constants.model_constant import MT_TEXT_EMBEDDING
from common.common_model.base import BaseModel, ModelResult


class TextEmbeddingBase(BaseModel):
    """文本向量抽象父类。走各家 OpenAI 兼容 /embeddings 端点（经 AsyncOpenAI）。

    ``max_batch_size`` 是**一次请求能塞几条文本**（厂商协议约束，各家不同）：
    通义 text-embedding-v3/v4 一次最多 10 条、智谱 embedding-3 最多 64 条，超了厂商
    直接 400（``InternalError.Algo.InvalidParameter: batch size is invalid, it should
    not be larger than 10.: input.contents``）。这里按子类声明的上限在基类内部顺序拆批，
    送多少条就拿回多少条且顺序不变——上层（rag 解析、工作流节点）不该为了某一家的上限
    改自己的批量策略。0/负数 = 不拆，按部署自己的上限发。

    ``max_item_chars`` 是**单条文本能送多长**（厂商 token 上限，各家不同）：通义
    text-embedding-v3/v4 每条最长 8192 token、智谱 embedding-3 每条 3072 token，超了厂商
    直接 400（``InternalError.Algo.InvalidParameter: Range of input length should be
    [1, 8192]``）。它与条数上限一样归本层兜：一条超长不是它自己失败，是**同一批里其余
    切片陪着一起失败**（上层拿不到等长向量就整篇文档 FAILED）。0/负数 = 不削。

    口径沿用 text_rerank：拿「字符」当最严口径而不是真算 token（中文一字一 token 时等价，
    英文场景只会更保守），不为此引一个分词器。

    一个必须知道的取舍：这里只削「送去向量化的文本」，ES/MySQL 里的切片正文一个字不动
    （展示与溯源仍完整）；代价是被削掉的那半截内容不再有任何向量语义——长块「命不中后半
    句」是这个设计的固有结果，想根治得把块切小（知识库的分块配置），不是在这里放宽上限。
    """

    category = MT_TEXT_EMBEDDING
    # 单次请求文本条数 / 单条长度上限：由各供应商子类按自家协议声明，基类不猜
    max_batch_size: int = 0
    max_item_chars: int = 0

    async def ainvoke(self, input: Union[str, list] = None, **kwargs) -> ModelResult:
        if input is None:
            raise ValueError("文本向量需提供 input")
        # 先削单条长度再拆批：一条对一条，削完条数与下标都不变，对齐不受影响
        input = self._fit(input) if isinstance(input, str) else [self._fit(t) for t in input]
        params = {k: v for k, v in {**self.extra, **kwargs}.items() if v is not None}
        size = int(self.max_batch_size or 0)
        if isinstance(input, str) or size < 1 or len(input) <= size:
            return self._to_result([await self._request(input, params)])
        # 逐批 await 而不是并发发：本层的并发由调用方限流（rag 的 embedding_concurrency），
        # 这里再并发等于绕过那层限制去打击上游
        items = list(input)
        responses = [await self._request(items[i:i + size], params)
                     for i in range(0, len(items), size)]
        return self._to_result(responses)

    def _fit(self, text: Any) -> Any:
        """按厂商单条长度上限取前缀（与 text_rerank._fit 同一个口径）。

        非字符串原样交出——长度不是这里能算的，也不该由这里猜。
        不记日志：一个宽表可能几十块全超长，逐条 warning 会把 worker 日志冲干净，
        而这件事的排查入口本来就应该是「这块内容为什么召不回」而不是「它被削过」。
        """
        limit = int(self.max_item_chars or 0)
        if not isinstance(text, str) or not limit or len(text) <= limit:
            return text
        return text[:limit]

    async def _request(self, input: Union[str, list], params: dict):
        """发一次 /embeddings 请求（拆批后每批一次）。"""
        return await self.oai().embeddings.create(
            model=self.model, input=input, **params)

    @staticmethod
    def _ordered(resp) -> list:
        """按回传的 index 还原本批内的顺序（OpenAI 规范本应同序，个别兼容实现不保证）。

        只在**每批内部**排序：index 是本次请求里的序号，跨批排会把两批拼成一团。
        """
        data = list(resp.data or [])
        if data and all(getattr(d, "index", None) is not None for d in data):
            data.sort(key=lambda d: d.index)
        return data

    def _to_result(self, responses: list) -> ModelResult:
        """多批响应合成一个 ModelResult：向量按发送顺序拼接，token 统计累加。

        拼接顺序就是拆批顺序，所以下标与入参严格对齐（rag 解析按 chunk_index 拼 ES 的
        _id，错一位是看不出来的静默脏数据）。
        """
        batches = [self._ordered(r) for r in responses]
        vectors = [d.embedding for data in batches for d in data]
        usage: dict = {}
        for r in responses:
            for key, value in (r.usage.model_dump()
                               if getattr(r, "usage", None) else {}).items():
                usage[key] = usage.get(key, 0) + value \
                    if isinstance(value, (int, float)) else value
        if len(responses) == 1:
            raw = responses[0].model_dump()
        else:
            # 拆过批就把每次请求的 data 摊平成同一个形状，另附 batches 说明来源
            raw = {"object": "list",
                   "data": [d.model_dump() for data in batches for d in data],
                   "usage": usage, "batches": len(responses)}
        return ModelResult(vectors=vectors, usage=usage, raw=raw)

    async def aparse(self, result: ModelResult) -> Any:
        """解析：单条输入返回一维向量，多条返回二维。"""
        vs = result.vectors or []
        return vs[0] if len(vs) == 1 else vs


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
