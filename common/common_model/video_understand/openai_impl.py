# -*- coding: utf-8 -*-
"""供应商 openai：通用视频对话。"""
from common.common_constants.model_constant import (MT_VIDEO_UNDERSTAND,
                                                   PROVIDER_OPENAI)
from common.common_model.base import register
from common.common_model.video_understand import VideoUnderstandBase


@register(MT_VIDEO_UNDERSTAND, PROVIDER_OPENAI)
class OpenAIVideoUnderstand(VideoUnderstandBase):
    provider = PROVIDER_OPENAI
    default_model = "gpt-4o"
