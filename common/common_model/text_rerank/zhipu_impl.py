# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：POST {base}/rerank（model=rerank）。"""
from common.common_constants.model_constant import MT_TEXT_RERANK, PROVIDER_ZHIPU
from common.common_model.base import ModelResult, http_client, register
from common.common_model.text_rerank import TextRerankBase


@register(MT_TEXT_RERANK, PROVIDER_ZHIPU)
class ZhipuRerank(TextRerankBase):
    provider = PROVIDER_ZHIPU
    default_model = "rerank"

    async def ainvoke(self, query: str, documents: list[str], top_n: int = None, **kwargs) -> ModelResult:
        body = {"model": self.model, "query": query, "documents": documents}
        if top_n:
            body["top_n"] = top_n
        body.update({**self.extra, **kwargs})
        resp = await http_client().post(
            self.config.base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        resp.raise_for_status()
        data = resp.json()
        return ModelResult(scores=data.get("results", []), raw=data)
