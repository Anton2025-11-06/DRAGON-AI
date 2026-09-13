# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：glm-4v-plus 视频理解。"""
from common.common_constants.model_constant import (MT_VIDEO_UNDERSTAND,
                                                   PROVIDER_ZHIPU)
from common.common_model.base import register
from common.common_model.video_understand import VideoUnderstandBase


@register(MT_VIDEO_UNDERSTAND, PROVIDER_ZHIPU)
class ZhipuVideoUnderstand(VideoUnderstandBase):
    provider = PROVIDER_ZHIPU
    default_model = "glm-4v-plus"
