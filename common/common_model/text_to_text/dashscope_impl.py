# -*- coding: utf-8 -*-
"""二级目录/供应商：通义千问 dashscope —— OpenAI 兼容模式 /compatible-mode/v1。"""
from common.common_constants.model_constant import (MT_TEXT_TO_TEXT,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import register
from common.common_model.text_to_text import TextToTextBase


@register(MT_TEXT_TO_TEXT, PROVIDER_DASHSCOPE)
class DashscopeTextToText(TextToTextBase):
    """通义文生文。深度思考通过 body.enable_thinking=true 开启（Qwen 推理模型）。"""

    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-turbo"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        # 通义思考模式：仅推理类模型支持，且通常要求 stream=True
        return {"extra_body": {"enable_thinking": True}} if thinking else {}
