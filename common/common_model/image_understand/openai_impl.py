# -*- coding: utf-8 -*-
"""供应商 openai：通用视觉对话（gpt-4o 等）。"""
from common.common_constants.model_constant import (MT_IMAGE_UNDERSTAND,
                                                   PROVIDER_OPENAI)
from common.common_model.base import register
from common.common_model.image_understand import ImageUnderstandBase


@register(MT_IMAGE_UNDERSTAND, PROVIDER_OPENAI)
class OpenAIImageUnderstand(ImageUnderstandBase):
    provider = PROVIDER_OPENAI
    default_model = "gpt-4o"
