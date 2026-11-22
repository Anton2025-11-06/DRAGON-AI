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
        # 与 text_to_text/dashscope 同一颗坑：Qwen3-vl 混合推理模型兼容端 enable_thinking
        # 默认 true，图片增强走非流式，不显式关掉就会每图撞满 120s 超时。同样按
        # supports_thinking 闸门双向外发，未登记（qwen-vl-plus 等）不传。
        if not getattr(self.config, "supports_thinking", False):
            return {}
        return {"extra_body": {"enable_thinking": bool(thinking)}}
