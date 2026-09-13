# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：cogview-3-flash，走 OpenAI 兼容 /images/generations（同步返回 url）。"""
from common.common_constants.model_constant import MT_TEXT_TO_IMAGE, PROVIDER_ZHIPU
from common.common_model.base import ModelResult, register
from common.common_model.text_to_image import TextToImageBase


@register(MT_TEXT_TO_IMAGE, PROVIDER_ZHIPU)
class ZhipuTextToImage(TextToImageBase):
    provider = PROVIDER_ZHIPU
    default_model = "cogview-3-flash"

    async def ainvoke(self, prompt: str, size: str = "1024x1024", **kwargs) -> ModelResult:
        resp = await self.oai().images.generate(
            model=self.model, prompt=prompt, size=size,
            **{k: v for k, v in {**self.extra, **kwargs}.items() if v is not None})
        url = resp.data[0].url if resp.data else None
        return ModelResult(url=url, raw=resp.model_dump())
