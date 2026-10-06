# -*- coding: utf-8 -*-
"""一级目录：文本重排（text_rerank，不支持 stream）。

各家 rerank 端点/报文形态不同（非 OpenAI 标准），故供应商子类只实现「发一次请求」
（``_invoke_batch``，无桥接）；「一次能送几条、送多长」是厂商协议约束，统一由基类
按声明的上限拆批、再把各批分数拼回与入参下标对齐的一份结果。
openai 通用客户端不注册此类型。
"""
from __future__ import annotations

import math
from typing import Any, Optional

from common.common_constants.model_constant import MT_TEXT_RERANK
from common.common_model.base import BaseModel, ModelResult


def _est_tokens(text: Any) -> int:
    """粗估 token 数：非 ASCII（中/日/韩等）按一字一 token，ASCII 四字符一 token。

    只用来决定拆批边界，不追求精确：宁可估多（多分几批、多几次请求），也不能估少
    （估少就是带着超预算的报文发出去，厂商直接 400，整组候选都排不上序）。
    """
    body = text if isinstance(text, str) else str(text or "")
    ascii_chars = sum(1 for ch in body if ch.isascii())
    return max(1, math.ceil(ascii_chars / 4) + len(body) - ascii_chars)


class TextRerankBase(BaseModel):
    """文本重排抽象父类。relevance_score 结果统一放 ModelResult.scores。

    下面三个上限都是厂商**单次请求**的协议约束，超了不是慢一点，是整次调用失败：

    - ``max_documents``：一次最多几条文档。通义 gte-rerank-v2 / qwen3-rerank = 500，
      智谱 rerank = 128。
    - ``max_request_tokens``：一次请求的 token 预算，厂商公式是
      「query token × 文档条数 + 各文档 token 之和」（gte-rerank-v2 = 30,000）。
      候选几百条、每条上千字时，先撞的反而是这条。
    - ``max_item_chars``：单条（query 或 document）长度上限，超了厂商 400。通义按
      4,000 token、智谱按 4,096 字符计，这里统一取「字符」作最严口径（一字一 token
      的中文场景等价，英文场景只会更保守）。

    基类据此把候选装成几批、逐批打分，再把每批回传的**批内** index 换算成全局下标：
    调用方拿到的仍是「index 就是入参下标」的一份分数，不需要知道分了几次。
    0/负数 = 该项不限（按部署自己的上限发）。

    一个如实的取舍：厂商说明 relevance_score 只在本次请求内可比。分批后跨批的降序
    是近似（同一个 query 下 pointwise 打分通常仍可比），换来的是「候选多少都能重排」，
    而不是整组退回融合分数序。
    """

    category = MT_TEXT_RERANK
    # 单次请求的条数/token/单条长度上限：由各供应商子类按自家协议声明，基类不猜
    max_documents: int = 0
    max_request_tokens: int = 0
    max_item_chars: int = 0

    async def ainvoke(self, query: str, documents: list[str], top_n: int = None,
                      **kwargs) -> ModelResult:
        query, docs = self._fit(query), [self._fit(d) for d in (documents or [])]
        if not docs:
            # 空候选没必要去敲厂商的门（多数厂商对空数组直接 400）
            return ModelResult(scores=[], raw={})
        parts = []
        for batch in self._pack(query, docs):
            # 每批都全量打分：批内 top_n 传这一批的条数，最后再按调用方要的 top_n 截
            parts.append((batch, await self._invoke_batch(query, batch,
                                                          top_n=len(batch), **kwargs)))
        return self._merge(parts, top_n)

    async def _invoke_batch(self, query: str, documents: list,
                            top_n: int = None, **kwargs) -> ModelResult:
        """发一次 rerank 请求（供应商子类实现）：报文各家不同，分数形状统一。"""
        raise NotImplementedError

    def _fit(self, text: Any) -> Any:
        """按厂商单条长度上限取前缀。

        只改「送去打分的文本」，不动库里的切片内容；整条超长会让厂商 400，连带同一批
        里其余候选一起排不上序，用前缀打的分虽然粗，至少候选之间仍然可比。
        非字符串（多模态 rerank 那种 {"image": url} 形态）原样交出，长度不是这里能算的。
        """
        limit = int(self.max_item_chars or 0)
        if not isinstance(text, str) or not limit or len(text) <= limit:
            return text
        return text[:limit]

    def _pack(self, query: str, docs: list) -> list[list]:
        """把候选按「条数上限 + 请求 token 预算」装批：装不下才另起一批，顺序不变。"""
        size = int(self.max_documents or 0)
        budget = int(self.max_request_tokens or 0)
        q_tokens = _est_tokens(query)
        batches: list[list[str]] = [[]]
        cost = 0
        for doc in docs:
            # 厂商公式里 query 要按条数计，所以每加一条的增量是「query 那份 + 这条本身」
            delta = q_tokens + _est_tokens(doc)
            if batches[-1] and ((size and len(batches[-1]) >= size)
                                or (budget and cost + delta > budget)):
                batches.append([])
                cost = 0
            batches[-1].append(doc)
            cost += delta
        return [b for b in batches if b]

    @staticmethod
    def _merge(parts: list, top_n: Optional[int]) -> ModelResult:
        """各批分数合成一份：批内 index → 全局下标，按分数降序，再按 top_n 截。"""
        scores: list[dict] = []
        usage: dict = {}
        raws: list[Any] = []
        offset = 0
        for batch, result in parts:
            for item in (result.scores or []):
                if not isinstance(item, dict) or item.get("index") is None:
                    # 没有 index 就定不了位（个别私有化实现只回分数），宁可不给这条分
                    continue
                idx = int(item["index"])
                if 0 <= idx < len(batch):
                    scores.append({**item, "index": offset + idx})
            for key, value in (result.usage or {}).items():
                usage[key] = usage.get(key, 0) + value \
                    if isinstance(value, (int, float)) else value
            raws.append(result.raw)
            offset += len(batch)
        scores.sort(key=lambda s: float(s.get("relevance_score") or 0.0), reverse=True)
        if top_n:
            scores = scores[:max(1, int(top_n))]
        raw = raws[0] if len(raws) == 1 else {"results": scores, "usage": usage,
                                             "batches": len(raws)}
        return ModelResult(scores=scores, usage=usage, raw=raw)

    async def aparse(self, result: ModelResult) -> Any:
        """解析：返回 [{'index','relevance_score'}] 列表（按分数降序）。"""
        return result.scores or []


from . import dashscope_impl, zhipu_impl  # noqa: E402,F401
