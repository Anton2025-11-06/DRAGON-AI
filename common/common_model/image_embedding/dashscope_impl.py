# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 multimodal-embedding-v1（1024 维）。"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import (MT_IMAGE_EMBEDDING,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import ModelResult, http_client, register
from common.common_model.image_embedding import ImageEmbeddingBase

_MM_EMB_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


@register(MT_IMAGE_EMBEDDING, PROVIDER_DASHSCOPE)
class DashscopeImageEmbedding(ImageEmbeddingBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "multimodal-embedding-v1"

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def ainvoke(self, image_urls: list[str] = None, image_url: str = None, **kwargs) -> ModelResult:
        urls = image_urls or ([image_url] if image_url else [])
        contents = [{"image": u} for u in urls] or [{"text": kwargs.pop("text", "")}]
        body = {"model": self.model, "input": {"contents": contents}, **self.extra}
        resp = await http_client().post(
            self._origin() + _MM_EMB_PATH,
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        resp.raise_for_status()
        data = resp.json()
        vectors = [e["embedding"] for e in data.get("output", {}).get("embeddings", [])]
        usage = data.get("usage", {})
        return ModelResult(vectors=vectors, usage=usage, raw=data)
