# -*- coding: utf-8 -*-
"""通用文件能力包：文档文本解析与结果 DTO（多模块共享）。

存储能力在 common.common_storage（本地/MinIO 等 S3 后端），
用法：from common.common_file import FileUtils
"""
from common.common_file.file_utils import FileUtils

__all__ = ["FileUtils"]