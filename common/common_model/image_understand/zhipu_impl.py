# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：glm-4v 系列多模态 chat。"""
from common.common_constants.model_constant import (MT_IMAGE_UNDERSTAND,
                                                   PROVIDER_ZHIPU)
from common.common_model.base import register
from common.common_model.image_understand import ImageUnderstandBase


@register(MT_IMAGE_UNDERSTAND, PROVIDER_ZHIPU)
class ZhipuImageUnderstand(ImageUnderstandBase):
    provider = PROVIDER_ZHIPU
    default_model = "glm-4v-flash"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        return {"extra_body": {"thinking": {"type": "enabled"}}} if thinking else {}
