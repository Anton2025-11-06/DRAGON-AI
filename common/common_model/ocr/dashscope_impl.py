# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：qwen-vl-ocr 专用 OCR 模型。"""
from common.common_constants.model_constant import MT_OCR, PROVIDER_DASHSCOPE
from common.common_model.base import register
from common.common_model.ocr import OCRBase


@register(MT_OCR, PROVIDER_DASHSCOPE)
class DashscopeOCR(OCRBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-vl-ocr"
