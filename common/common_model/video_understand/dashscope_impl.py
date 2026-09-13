# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：qwen-vl-plus/max 视频理解。"""
from common.common_constants.model_constant import (MT_VIDEO_UNDERSTAND,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import register
from common.common_model.video_understand import VideoUnderstandBase


@register(MT_VIDEO_UNDERSTAND, PROVIDER_DASHSCOPE)
class DashscopeVideoUnderstand(VideoUnderstandBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-vl-plus"
