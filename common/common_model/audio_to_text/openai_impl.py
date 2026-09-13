# -*- coding: utf-8 -*-
"""供应商 openai：标准 /audio/transcriptions（whisper-1）。"""
import io

from common.common_constants.model_constant import MT_AUDIO_TO_TEXT, PROVIDER_OPENAI
from common.common_model.base import ModelResult, http_client, register
from common.common_model.audio_to_text import AudioToTextBase


@register(MT_AUDIO_TO_TEXT, PROVIDER_OPENAI)
class OpenAIAudioToText(AudioToTextBase):
    provider = PROVIDER_OPENAI
    default_model = "whisper-1"

    async def ainvoke(self, audio_url: str = None, audio_bytes: bytes = None,
                      filename: str = "audio.wav", **kwargs) -> ModelResult:
        data = audio_bytes or (await http_client().get(audio_url)).content
        resp = await self.oai().audio.transcriptions.create(
            model=self.model, file=(filename, io.BytesIO(data)))
        return ModelResult(content=resp.text, raw=resp.model_dump())
