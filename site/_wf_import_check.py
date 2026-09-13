# -*- coding: utf-8 -*-
"""导入 + 支持矩阵自检：确保底层删除 is_direct/suffixes 后 common_model 与工作流引擎可正常导入。"""
import sys
sys.path.insert(0, r"D:\ai大模型\DRAGON-AI-master")

# 1) common_model 底层
from common.common_model import entry as cm
from common.common_model.model_types import ModelConfig
from common.common_model.base import ModelRegistry, ModelResult

# 2) 工作流引擎统一入口
from service.service_workflow.workflow_engine.model_client import (
    WorkflowModelClient, ChatMessage, ModelConfig as MC2,
)
from service.service_workflow.workflow_engine.nodes import ai_nodes, data_nodes

# 3) ModelConfig 不再含 is_direct/suffixes/use_suffix
fields = set(ModelConfig.__dataclass_fields__.keys())
assert "is_direct" not in fields, "is_direct 未删除"
assert "suffixes" not in fields, "suffixes 未删除"
assert "use_suffix" not in fields, "use_suffix 未删除"
assert not hasattr(ModelConfig, "model_url"), "model_url() 未删除"
print("[OK] ModelConfig fields:", sorted(fields))

# 4) 支持矩阵（动态发现）
matrix = ModelRegistry.matrix()
total = sum(len(v) for v in matrix.values())
print(f"[OK] 注册组合总数 = {total}")
for cat in sorted(matrix):
    print(f"   {cat:20s} -> {matrix[cat]}")

print("\n[OK] 全部导入 & 断言通过")
