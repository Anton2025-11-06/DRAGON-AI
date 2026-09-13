# -*- coding: utf-8 -*-
"""供应商 openai：通用 OpenAI /images/generations。"""
from common.common_constants.model_constant import MT_TEXT_TO_IMAGE, PROVIDER_OPENAI
from common.common_model.base import ModelResult, register
from common.common_model.text_to_image import TextToImageBase


@register(MT_TEXT_TO_IMAGE, PROVIDER_OPENAI)
class OpenAITextToImage(TextToImageBase):
    provider = PROVIDER_OPENAI
    default_model = "dall-e-3"

    async def ainvoke(self, prompt: str, size: str = "1024x1024", **kwargs) -> ModelResult:
        resp = await self.oai().images.generate(
            model=self.model, prompt=prompt, size=size,
            **{k: v for k, v in {**self.extra, **kwargs}.items() if v is not None})
        url = resp.data[0].url if resp.data else None
        return ModelResult(url=url, raw=resp.model_dump())
