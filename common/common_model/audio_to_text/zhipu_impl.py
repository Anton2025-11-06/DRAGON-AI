# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：glm-asr，OpenAI 兼容 /audio/transcriptions（multipart 上传音频）。"""
from common.common_constants.model_constant import MT_AUDIO_TO_TEXT, PROVIDER_ZHIPU
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.audio_to_text import AudioToTextBase


@register(MT_AUDIO_TO_TEXT, PROVIDER_ZHIPU)
class ZhipuAudioToText(AudioToTextBase):
    provider = PROVIDER_ZHIPU
    default_model = "glm-asr"

    async def ainvoke(self, audio_url: str = None, audio_bytes: bytes = None,
                      filename: str = "audio.wav", **kwargs) -> ModelResult:
        data = audio_bytes
        if data is None:
            data = (await http_client().get(audio_url)).content
        resp = await http_client().post(
            self.config.base_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            data={"model": self.model}, files={"file": (filename, data, "audio/wav")})
        ensure_ok(resp, "音频转文字调用")
        j = resp.json()
        return ModelResult(content=j.get("text", "") or j.get("message", ""), raw=j)
