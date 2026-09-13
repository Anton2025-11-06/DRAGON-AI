# -*- coding: utf-8 -*-
"""一级目录：图片理解（image_understand，支持 stream）。"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from common.common_constants.model_constant import MT_IMAGE_UNDERSTAND
from common.common_model.base import (BaseModel, ChatMLMixin, ModelResult,
                                      StreamableMixin)
from common.common_model.model_types import StreamChunk


class ImageUnderstandBase(BaseModel, ChatMLMixin, StreamableMixin):
    """图片理解抽象父类。入参 prompt + image_urls，走多模态 chat。"""

    category = MT_IMAGE_UNDERSTAND

    def _messages(self, prompt: str, image_urls: list[str]) -> list:
        content = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
        return [{"role": "user", "content": content}]

    async def ainvoke(self, prompt: str, image_urls: list[str] = None,
                      image_url: Optional[str] = None, thinking: bool = False, **kwargs) -> ModelResult:
        urls = image_urls or ([image_url] if image_url else [])
        return await self._chat_invoke(self._messages(prompt, urls), thinking=thinking, **kwargs)

    async def astream(self, prompt: str, image_urls: list[str] = None,
                      image_url: Optional[str] = None, thinking: bool = False, **kwargs) -> AsyncIterator[StreamChunk]:
        urls = image_urls or ([image_url] if image_url else [])
        async for c in self._chat_stream(self._messages(prompt, urls), thinking=thinking, **kwargs):
            yield c

    async def aparse(self, result: ModelResult) -> Any:
        return result.content


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
