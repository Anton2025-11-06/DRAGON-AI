# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 gte-rerank 端点。

rerank 属原生 api/v1，不在 compatible-mode 下；从 config.base_url 派生原生 origin。
单次请求的条数/长度上限由基类拆批兜住（见 TextRerankBase）。
"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import (MT_TEXT_RERANK,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.text_rerank import TextRerankBase

_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


@register(MT_TEXT_RERANK, PROVIDER_DASHSCOPE)
class DashscopeRerank(TextRerankBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "gte-rerank-v2"

    # 官方单次请求上限（百炼「重排序」模型概览）：gte-rerank-v2 最多 500 条、
    # 请求 token 预算 30,000（公式：query token × 条数 + 文档 token 之和）、
    # 单条 4,000 token。登记的换成 qwen3-rerank 时条数不变、预算放宽到 120,000，
    # 想少分几批就在子类属性上改这两个数。
    max_documents = 500
    max_request_tokens = 30000
    max_item_chars = 4000

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def _invoke_batch(self, query: str, documents: list, top_n: int = None,
                            **kwargs) -> ModelResult:
        body = {"model": self.model, "input": {"query": query, "documents": documents},
                "parameters": {"return_documents": False}}
        if top_n:
            body["parameters"]["top_n"] = top_n
        resp = await http_client().post(
            self._origin() + _RERANK_PATH,
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        ensure_ok(resp, "文本重排调用")
        data = resp.json()
        return ModelResult(scores=data.get("output", {}).get("results", []),
                           usage=data.get("usage") or {}, raw=data)
