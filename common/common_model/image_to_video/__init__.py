# -*- coding: utf-8 -*-
"""一级目录：图生视频（image_to_video，不支持 stream）。"""
from __future__ import annotations

from typing import Any

from common.common_constants.model_constant import MT_IMAGE_TO_VIDEO
from common.common_model.base import BaseModel, ModelResult


class ImageToVideoBase(BaseModel):
    """图生视频抽象父类（异步提交-轮询）。"""

    category = MT_IMAGE_TO_VIDEO

    async def aparse(self, result: ModelResult) -> Any:
        return result.url


from . import dashscope_impl, zhipu_impl  # noqa: E402,F401
