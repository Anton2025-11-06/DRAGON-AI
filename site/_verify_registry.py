# -*- coding: utf-8 -*-
"""导入 common_model，校验 12 类型注册矩阵（无真实调用）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_constants.model_constant import MODEL_TYPES_ALL, MODEL_TYPE_LABELS
from common.common_model import matrix, providers_for, categories_for
from common.common_model.base import ModelRegistry

m = matrix()
print("== 支持矩阵（动态注册结果）==")
for cat in MODEL_TYPES_ALL:
    provs = providers_for(cat)
    print(f"{MODEL_TYPE_LABELS[cat]:8s} {cat:18s} -> {provs}")
print("\n== 反向：各供应商支持的类型数 ==")
for p in ["openai", "dashscope", "zhipu"]:
    cats = categories_for(p)
    print(f"{p:10s} {len(cats)}/12 -> {cats}")

# 校验注册组合总数
total = sum(len(v) for v in m.values())
print("\n注册组合总数(类型×供应商):", total)
