# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 MultiModalEmbedding（文本+图片+视频 → 同空间向量）。

端点：POST {origin}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding
入参 input.contents = [{"text":..},{"image":..},{"video":..}] 任意组合；
出参 output.embeddings[] = [{index, type, embedding}]（默认每段内容一个独立向量）。
默认模型 multimodal-embedding-v1（1024 维，固定）；qwen3-vl-embedding 等可经
common_params 传 dimension/enable_fusion（透传到 parameters）。
"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import (MT_MULTIMODAL_EMBEDDING,
                                                    PROVIDER_DASHSCOPE)
from common.common_model.base import ModelResult, http_client, register, ensure_ok
from common.common_model.model_types import ModelInvokeError
from common.common_model.multimodal_embedding import MultimodalEmbeddingBase

_MM_EMB_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


@register(MT_MULTIMODAL_EMBEDDING, PROVIDER_DASHSCOPE)
class DashscopeMultimodalEmbedding(MultimodalEmbeddingBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "multimodal-embedding-v1"

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def ainvoke(self, text=None, texts=None, image_urls=None, image_url=None,
                      video_urls=None, video_url=None, **kwargs) -> ModelResult:
        # 归一为 contents 列表：文本 → 图片 → 视频（同一次请求返回各自独立向量）
        contents = []
        for t in ([text] if text else []) + (texts or []):
            if t:
                contents.append({"text": t})
        for u in ([image_url] if image_url else []) + (image_urls or []):
            if u:
                contents.append({"image": u})
        for v in ([video_url] if video_url else []) + (video_urls or []):
            if v:
                contents.append({"video": v})
        if not contents:
            raise ValueError("多模态向量需提供 text/image/video 至少一种输入")

        body = {"model": self.model, "input": {"contents": contents}}
        params = {k: v for k, v in {**self.extra, **kwargs}.items() if v is not None}
        if params:
            body["parameters"] = params
        resp = await http_client().post(
            self._origin() + _MM_EMB_PATH,
            headers={"Authorization": f"Bearer {self.config.api_key}"}, json=body)
        ensure_ok(resp, "多模态向量调用")
        data = resp.json()
        embs = data.get("output", {}).get("embeddings", [])
        # 按输入索引排序，保证向量顺序与 contents 一致
        vectors = [e["embedding"] for e in sorted(embs, key=lambda e: e.get("index", 0))]
        usage = data.get("usage", {})
        return ModelResult(vectors=vectors, usage=usage, raw=data)
