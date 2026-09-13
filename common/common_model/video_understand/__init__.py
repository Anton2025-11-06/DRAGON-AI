# -*- coding: utf-8 -*-
"""一级目录：视频理解（video_understand，支持 stream）。"""
from __future__ import annotations

from typing import Any, AsyncIterator

from common.common_constants.model_constant import MT_VIDEO_UNDERSTAND
from common.common_model.base import (BaseModel, ChatMLMixin, ModelResult,
                                      StreamableMixin)
from common.common_model.model_types import StreamChunk


class VideoUnderstandBase(BaseModel, ChatMLMixin, StreamableMixin):
    """视频理解抽象父类。入参 prompt + video_url，走多模态 chat。"""

    category = MT_VIDEO_UNDERSTAND

    def _messages(self, prompt: str, video_url: str) -> list:
        return [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": video_url}},
            {"type": "text", "text": prompt}]}]

    async def ainvoke(self, prompt: str, video_url: str, thinking: bool = False, **kwargs) -> ModelResult:
        return await self._chat_invoke(self._messages(prompt, video_url), thinking=thinking, **kwargs)

    async def astream(self, prompt: str, video_url: str, thinking: bool = False, **kwargs) -> AsyncIterator[StreamChunk]:
        async for c in self._chat_stream(self._messages(prompt, video_url), thinking=thinking, **kwargs):
            yield c

    async def aparse(self, result: ModelResult) -> Any:
        return result.content


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
