# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：qwen-tts（原生 multimodal-generation，返回音频 url）。"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import MT_TEXT_TO_AUDIO, PROVIDER_DASHSCOPE
from common.common_model.base import (ModelResult, ensure_ok, http_client,
                                      register)
from common.common_model.text_to_audio import TextToAudioBase

_GEN = "/api/v1/services/aigc/multimodal-generation/generation"


@register(MT_TEXT_TO_AUDIO, PROVIDER_DASHSCOPE)
class DashscopeTextToAudio(TextToAudioBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-tts"

    async def ainvoke(self, text: str, voice: str = "Cherry", **kwargs) -> ModelResult:
        s = urlsplit(self.config.base_url)
        origin = f"{s.scheme}://{s.netloc}"
        resp = await http_client().post(origin + _GEN,
                                        headers={"Authorization": f"Bearer {self.config.api_key}"},
                                        json={"model": self.model,
                                              "input": {"text": text, "voice": voice},
                                              "parameters": {**self.extra}})
        ensure_ok(resp, "文生音频调用")
        data = resp.json()
        url = data.get("output", {}).get("audio", {}).get("url")
        return ModelResult(url=url, raw=data)
