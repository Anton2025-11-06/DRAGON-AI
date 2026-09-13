# -*- coding: utf-8 -*-
"""二级目录/供应商：智谱 zhipu —— OpenAI 兼容端点 /api/paas/v4（经 AsyncOpenAI）。

放弃官方 zai-sdk/zhipuai（内部全同步、性能差）；直接用 AsyncOpenAI 打智谱兼容端点。
"""
from common.common_constants.model_constant import (MT_TEXT_TO_TEXT,
                                                    PROVIDER_ZHIPU)
from common.common_model.base import register
from common.common_model.text_to_text import TextToTextBase


@register(MT_TEXT_TO_TEXT, PROVIDER_ZHIPU)
class ZhipuTextToText(TextToTextBase):
    """智谱文生文。深度思考通过 body.thinking={"type":"enabled"} 开启。"""

    provider = PROVIDER_ZHIPU
    default_model = "glm-4-flash"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        # 智谱 GLM 思考模式：非流式需模型支持；此处按文档注入 extra_body.thinking
        return {"extra_body": {"thinking": {"type": "enabled"}}} if thinking else {}
