# -*- coding: utf-8 -*-
import os
import re

ROOT = r"D:\ai大模型\DRAGON-AI-master\ui-ai\apps\web-antd\src"
PAT = re.compile(r"isDirect|is_direct|suffix|Suffix|MODEL_SUFFIX|selectedSuffix|use_suffix")
hits = []
for dp, dn, fn in os.walk(ROOT):
    if "node_modules" in dp:
        continue
    for f in fn:
        if not f.endswith((".ts", ".vue")):
            continue
        p = os.path.join(dp, f)
        try:
            with open(p, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    if PAT.search(line):
                        rel = os.path.relpath(p, ROOT)
                        hits.append(f"{rel}:{i}:{line.strip()}")
        except Exception as e:
            hits.append(f"ERR {p}: {e}")
out = r"D:\ai大模型\DRAGON-AI-master\site\_g_fe4.txt"
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(hits))
print("COUNT=" + str(len(hits)))
