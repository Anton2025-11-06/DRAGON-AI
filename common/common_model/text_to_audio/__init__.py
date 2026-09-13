# -*- coding: utf-8 -*-
"""一级目录：文生音频（text_to_audio，不支持 stream）。"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_TEXT_TO_AUDIO
from common.common_model.base import BaseModel, ModelResult


class TextToAudioBase(BaseModel):
    """文生音频抽象父类。产物音频字节放 audio_bytes，直链放 url。"""

    category = MT_TEXT_TO_AUDIO

    async def aparse(self, result: ModelResult) -> Any:
        return result.audio_bytes if result.audio_bytes is not None else result.url


from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
