# -*- coding: utf-8 -*-
import os
ROOT = r"D:\ai大模型\DRAGON-AI-master\common\common_model"
lines = []
for r, d, fs in os.walk(ROOT):
    for f in sorted(fs):
        if f.endswith(".py"):
            lines.append(os.path.relpath(os.path.join(r, f), ROOT))
with open(r"D:\ai大模型\DRAGON-AI-master\site\_cm_files.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("N=" + str(len(lines)))
