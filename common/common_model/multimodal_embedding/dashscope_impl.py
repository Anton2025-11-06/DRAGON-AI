# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 MultiModalEmbedding（文本+图片+视频 → 同空间向量）。

端点：POST {origin}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding
入参 input.contents = [{"text":..},{"image":..},{"video":..}] 任意组合；
出参 output.embeddings[] = [{index, type, embedding}]（默认每段内容一个独立向量）。
默认模型 multimodal-embedding-v1（1024 维，固定）；qwen3-vl-embedding 等可经
common_params 传 dimension/enable_fusion（透传到 parameters）。
contents 的单请求条数配额由基类拆批（见 MultimodalContentsMixin），发送顺序
就是返回顺序。
"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import (MT_MULTIMODAL_EMBEDDING,
                                                    PROVIDER_DASHSCOPE)
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.multimodal_embedding import MultimodalEmbeddingBase

_MM_EMB_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


@register(MT_MULTIMODAL_EMBEDDING, PROVIDER_DASHSCOPE)
class DashscopeMultimodalEmbedding(MultimodalEmbeddingBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "multimodal-embedding-v1"

    # 官方单请求配额（multimodal-embedding-v1）：内容元素总数 ≤ 20，其中图片、视频
    # 各最多 1 条、文本最多 20 条——所以「一批 16 张图」在这一层就是一张一次地发。
    # 换成 tongyi-embedding-vision-plus（图片可 8 张）就改这四个类属性。
    max_contents = 20
    max_texts = 20
    max_images = 1
    max_videos = 1

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def ainvoke(self, text=None, texts=None, image_urls=None, image_url=None,
                      video_urls=None, video_url=None, **kwargs) -> ModelResult:
        # 归一为 contents 列表：文本 → 图片 → 视频（每一项各自返回一个独立向量）
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

        params = {k: v for k, v in {**self.extra, **kwargs}.items() if v is not None}
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        # 逐批 await 而不是并发发：本层的并发由调用方限流（rag 的 embedding_concurrency），
        # 这里再并发等于绕过那层限制去打击上游
        responses = []
        for batch in self._pack_contents(contents):
            body = {"model": self.model, "input": {"contents": batch}}
            if params:
                body["parameters"] = params
            resp = await http_client().post(self._origin() + _MM_EMB_PATH,
                                            headers=headers, json=body)
            ensure_ok(resp, "多模态向量调用")
            responses.append(resp.json())
        return self._contents_result(responses)
