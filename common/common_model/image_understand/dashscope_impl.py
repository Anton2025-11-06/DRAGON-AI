# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：qwen-vl 系列多模态 chat。"""
from common.common_constants.model_constant import (MT_IMAGE_UNDERSTAND,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import register
from common.common_model.image_understand import ImageUnderstandBase


@register(MT_IMAGE_UNDERSTAND, PROVIDER_DASHSCOPE)
class DashscopeImageUnderstand(ImageUnderstandBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-vl-plus"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        return {"extra_body": {"enable_thinking": True}} if thinking else {}
