# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：glm-tts（voice=female），OpenAI 兼容 /audio/speech 直接返回字节流。"""
from common.common_constants.model_constant import MT_TEXT_TO_AUDIO, PROVIDER_ZHIPU
from common.common_model.base import ModelResult, http_client, register
from common.common_model.text_to_audio import TextToAudioBase


@register(MT_TEXT_TO_AUDIO, PROVIDER_ZHIPU)
class ZhipuTextToAudio(TextToAudioBase):
    provider = PROVIDER_ZHIPU
    default_model = "glm-tts"

    async def ainvoke(self, text: str, voice: str = "female",
                      response_format: str = "wav", **kwargs) -> ModelResult:
        resp = await http_client().post(
            self.config.base_url.rstrip("/") + "/audio/speech",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            json={"model": self.model, "input": text, "voice": voice,
                  "response_format": response_format, **self.extra})
        resp.raise_for_status()
        return ModelResult(audio_bytes=resp.content, raw={"format": response_format, "size": len(resp.content)})
