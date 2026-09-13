# -*- coding: utf-8 -*-
"""二级目录/供应商：openai —— 通用 OpenAI 兼容客户端（base_url 由配置决定）。"""
from common.common_constants.model_constant import PROVIDER_OPENAI
from common.common_model.base import register
from common.common_model.text_to_text import TextToTextBase


@register("text_to_text", PROVIDER_OPENAI)
class OpenAITextToText(TextToTextBase):
    """通用 OpenAI 协议文生文。base_url/api_key/model_name 全部来自 ModelConfig，
    不绑定任何具体厂商；深度思考由所选模型自身能力决定（此处不额外注入参数）。
    """

    provider = PROVIDER_OPENAI
    default_model = "gpt-4o-mini"
