# -*- coding: utf-8 -*-
"""后端删除 is_direct/suffixes 后的整体导入自检 + 类别接口数据源确认。"""
import sys
sys.path.insert(0, r"D:\ai大模型\DRAGON-AI-master")

import service.service_system.services.model_service as ms
import service.service_gateway.util.model_proxy_router as mpr
import service.service_gateway.util.model_gateway_cache as mgc
import service.service_workflow.services.workflow_service as wfs
import service.service_system.schemas.model_schema as sch
import common.common_constants.model_constant as mc

# schema 不应再有 suffix / is_direct / suffix_url / ModelSuffixItem
for attr in ("ModelSuffixItem",):
    assert not hasattr(sch, attr), f"schema 仍含 {attr}"
save_fields = set(sch.ModelSaveRequest.model_fields.keys())
test_fields = set(sch.ModelTestRequest.model_fields.keys())
assert "is_direct" not in save_fields and "suffixes" not in save_fields, save_fields
assert "suffix_url" not in test_fields, test_fields

# 常量层不再有 MODEL_ENDPOINT_* 后缀常量
for attr in ("MODEL_ENDPOINT_CHAT", "MODEL_ENDPOINT_DEFAULT_CHAT"):
    assert not hasattr(mc, attr), f"常量仍含 {attr}"

print("CATEGORIES(12):", list(ms.CATEGORIES.keys()))
print("PROVIDERS(3):", list(ms.PROVIDERS.keys()))
print("save_fields:", sorted(save_fields))
print("test_fields:", sorted(test_fields))
print("\n[OK] 后端导入 & 断言全部通过")
