# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：glm-ocr 专用 OCR 模型。"""
from common.common_constants.model_constant import MT_OCR, PROVIDER_ZHIPU
from common.common_model.base import register
from common.common_model.ocr import OCRBase


@register(MT_OCR, PROVIDER_ZHIPU)
class ZhipuOCR(OCRBase):
    provider = PROVIDER_ZHIPU
    # glm-ocr 对图片格式/来源有严格校验(仅PDF/JPG/PNG、≤10MB)，远程URL常被判格式不符；
    # 实测 glm-4v-flash 通用视觉模型可稳定完成文字提取，故默认用它。
    default_model = "glm-4v-flash"
