# -*- coding: utf-8 -*-
"""一级目录：文生图（text_to_image，不支持 stream）。"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_TEXT_TO_IMAGE
from common.common_model.base import BaseModel, ModelResult


class TextToImageBase(BaseModel):
    """文生图抽象父类。产物图片直链放 ModelResult.url/urls。"""

    category = MT_TEXT_TO_IMAGE

    async def aparse(self, result: ModelResult) -> Any:
        return result.url or (result.urls[0] if result.urls else None)


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
