# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 gte-rerank 端点。

rerank 属原生 api/v1，不在 compatible-mode 下；从 config.base_url 派生原生 origin。
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

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def ainvoke(self, query: str, documents: list[str], top_n: int = None, **kwargs) -> ModelResult:
        body = {"model": self.model, "input": {"query": query, "documents": documents},
                "parameters": {"return_documents": False}}
        if top_n:
            body["parameters"]["top_n"] = top_n
        resp = await http_client().post(
            self._origin() + _RERANK_PATH,
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        ensure_ok(resp, "文本重排调用")
        data = resp.json()
        return ModelResult(scores=data.get("output", {}).get("results", []), raw=data)
