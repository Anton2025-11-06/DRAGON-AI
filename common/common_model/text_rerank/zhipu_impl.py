# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：POST {base}/rerank（model=rerank）。"""
from common.common_constants.model_constant import MT_TEXT_RERANK, PROVIDER_ZHIPU
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.text_rerank import TextRerankBase


@register(MT_TEXT_RERANK, PROVIDER_ZHIPU)
class ZhipuRerank(TextRerankBase):
    provider = PROVIDER_ZHIPU
    default_model = "rerank"

    # 官方接口约束：documents 最多容纳 128 条，单条（query 也是）最长 4,096 字符。
    # 智谱没公布「整请求 token 预算」，所以 max_request_tokens 不声明（=0）。
    max_documents = 128
    max_item_chars = 4096

    async def _invoke_batch(self, query: str, documents: list, top_n: int = None,
                            **kwargs) -> ModelResult:
        body = {"model": self.model, "query": query, "documents": documents}
        if top_n:
            body["top_n"] = top_n
        body.update({**self.extra, **kwargs})
        resp = await http_client().post(
            self.config.base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        ensure_ok(resp, "文本重排调用")
        data = resp.json()
        return ModelResult(scores=data.get("results", []),
                           usage=data.get("usage") or {}, raw=data)
