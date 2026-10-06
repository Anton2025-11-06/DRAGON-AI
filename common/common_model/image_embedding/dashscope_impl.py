# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：原生 multimodal-embedding-v1（1024 维）。

与多模态向量同一个端点，因此同一套单请求配额也适用：图片一次最多 1 条，
多张图在这一层拆成多次请求，返回仍与送出的图同序。
"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import (MT_IMAGE_EMBEDDING,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.image_embedding import ImageEmbeddingBase

_MM_EMB_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


@register(MT_IMAGE_EMBEDDING, PROVIDER_DASHSCOPE)
class DashscopeImageEmbedding(ImageEmbeddingBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "multimodal-embedding-v1"

    # 官方配额（multimodal-embedding-v1）：元素总数 ≤ 20，其中图片最多 1 条。
    # 不声明这条的话，批量图片（哪怕只 2 张）就是整次 400。
    max_contents = 20
    max_texts = 20
    max_images = 1

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    async def ainvoke(self, image_urls: list[str] = None, image_url: str = None, **kwargs) -> ModelResult:
        urls = image_urls or ([image_url] if image_url else [])
        contents = [{"image": u} for u in urls] or [{"text": kwargs.pop("text", "")}]
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        # 逐批 await：并发由调用方限流，图片向量多用于解析后台任务，不抢上游
        responses = []
        for batch in self._pack_contents(contents):
            resp = await http_client().post(self._origin() + _MM_EMB_PATH, headers=headers,
                                            json={"model": self.model,
                                                  "input": {"contents": batch}, **self.extra})
            ensure_ok(resp, "图片向量调用")
            responses.append(resp.json())
        return self._contents_result(responses)
