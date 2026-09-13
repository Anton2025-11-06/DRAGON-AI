# -*- coding: utf-8 -*-
"""供应商 openai：标准 /audio/speech（tts-1）。"""
from common.common_constants.model_constant import MT_TEXT_TO_AUDIO, PROVIDER_OPENAI
from common.common_model.base import ModelResult, register
from common.common_model.text_to_audio import TextToAudioBase


@register(MT_TEXT_TO_AUDIO, PROVIDER_OPENAI)
class OpenAITextToAudio(TextToAudioBase):
    provider = PROVIDER_OPENAI
    default_model = "tts-1"

    async def ainvoke(self, text: str, voice: str = "alloy", **kwargs) -> ModelResult:
        # AsyncOpenAI.audio.speech.create 返回可 await 的字节响应
        resp = await self.oai().audio.speech.create(model=self.model, voice=voice, input=text)
        data = await resp.aread() if hasattr(resp, "aread") else resp.content
        return ModelResult(audio_bytes=data, raw={"voice": voice, "size": len(data)})
