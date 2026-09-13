# -*- coding: utf-8 -*-
"""输出 common_model 动态注册矩阵（category -> providers），落盘供检查。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_model import matrix
from common.common_constants.model_constant import MODEL_TYPES_ALL, PROVIDERS_ALL

m = matrix()
lines = []
total_reg = 0
for cat in MODEL_TYPES_ALL:
    provs = m.get(cat, [])
    total_reg += len(provs)
    lines.append(f"{cat:16s} -> {provs}")

lines.append("")
lines.append(f"注册组合总数 = {total_reg} / {len(MODEL_TYPES_ALL) * len(PROVIDERS_ALL)}")
lines.append("未注册组合 (12x3 中缺失):")
for cat in MODEL_TYPES_ALL:
    provs = set(m.get(cat, []))
    for p in PROVIDERS_ALL:
        if p not in provs:
            lines.append(f"  {cat} x {p}")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_matrix.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
