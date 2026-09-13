# -*- coding: utf-8 -*-
"""供应商 openai：通用视觉 OCR。"""
from common.common_constants.model_constant import MT_OCR, PROVIDER_OPENAI
from common.common_model.base import register
from common.common_model.ocr import OCRBase


@register(MT_OCR, PROVIDER_OPENAI)
class OpenAIOCR(OCRBase):
    provider = PROVIDER_OPENAI
    default_model = "gpt-4o"
