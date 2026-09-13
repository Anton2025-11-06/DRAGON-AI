# -*- coding: utf-8 -*-
"""一级目录：OCR（ocr，不支持 stream）。图片文字提取，走多模态 chat。"""
from __future__ import annotations

from typing import Any, Optional

from common.common_constants.model_constant import MT_OCR
from common.common_model.base import BaseModel, ChatMLMixin, ModelResult


class OCRBase(BaseModel, ChatMLMixin):
    """OCR 抽象父类。默认 prompt=提取全部文字。"""

    category = MT_OCR
    _DEFAULT_PROMPT = "请提取图片中所有文字，按原始排版输出。"

    def _messages(self, prompt: Optional[str], image_urls: list[str]) -> list:
        content = [{"type": "text", "text": prompt or self._DEFAULT_PROMPT}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
        return [{"role": "user", "content": content}]

    async def ainvoke(self, image_urls: list[str] = None, image_url: Optional[str] = None,
                      prompt: Optional[str] = None, **kwargs) -> ModelResult:
        urls = image_urls or ([image_url] if image_url else [])
        return await self._chat_invoke(self._messages(prompt, urls), **kwargs)

    async def aparse(self, result: ModelResult) -> Any:
        return result.content


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
