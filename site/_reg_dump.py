# -*- coding: utf-8 -*-
"""打印注册表验证通过清单（合并后）"""
import json

with open(r"D:\ai大模型\DRAGON-AI-master\site\_reg_verify_result.json", encoding="utf-8") as f:
    d = json.load(f)
total = 0
for p, cats in d["passed"].items():
    for c, ms in cats.items():
        for m in ms:
            print(f"{p:10s} {c:18s} {m}")
            total += 1
print(f"TOTAL = {total}")