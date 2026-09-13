# -*- coding: utf-8 -*-
"""一级目录：音频转文字（audio_to_text，不支持 stream）。"""
from __future__ import annotations

from typing import Any, Optional

from common.common_constants.model_constant import MT_AUDIO_TO_TEXT
from common.common_model.base import BaseModel, ModelResult


class AudioToTextBase(BaseModel):
    """音频转文字抽象父类。入参音频直链（或字节），结果文本放 content。"""

    category = MT_AUDIO_TO_TEXT

    async def aparse(self, result: ModelResult) -> Any:
        return result.content


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
