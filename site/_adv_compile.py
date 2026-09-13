# -*- coding: utf-8 -*-
"""编译校验：constant + service_file + model schema/service，确保高级设置改造后端可加载。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import py_compile  # noqa: E402

files = [
    "common/common_constants/constant.py",
    "service/service_file/__init__.py",
    "service/service_file/routers/file_router.py",
    "service/service_file/file.py",
    "service/service_system/models/model.py",
    "service/service_system/schemas/model_schema.py",
    "service/service_system/services/model_service.py",
]
for f in files:
    py_compile.compile(os.path.join(ROOT, f), doraise=True)
    print("compile OK:", f)

# 运行时导入校验
from common.common_constants.constant import (  # noqa: E402
    SERVICE_FILE, SERVICE_FILE_PORT, SERVICE_ALIASES,
)
print("SERVICE_FILE:", SERVICE_FILE, SERVICE_FILE_PORT, "alias:", SERVICE_ALIASES.get("file"))

import service.service_system.schemas.model_schema as sc  # noqa: E402
print("CommonParam fields:", list(sc.CommonParam.model_fields.keys()))
print("SaveReq has supports_stream:", "supports_stream" in sc.ModelSaveRequest.model_fields)
print("SaveReq base_url required:", sc.ModelSaveRequest.model_fields["base_url"].is_required())
print("TestReq has inputs:", "inputs" in sc.ModelTestRequest.model_fields)

import service.service_system.services.model_service as ms  # noqa: E402
print("derive:", ms.ModelService._derive_model_params([
    {"name": "temperature", "default": "0.7", "type": "number"},
    {"name": "stream", "default": "true", "type": "boolean"},
    {"name": "mode", "default": "fast", "type": "string"},
]))
print("ALL_OK")
