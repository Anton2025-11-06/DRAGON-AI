# -*- coding: utf-8 -*-
import os
import re

ROOT = r"D:\ai大模型\DRAGON-AI-master\service\service_system"
PAT = re.compile(r"def test|/test|ModelTestRequest|def categories|/categories|def my_keys|my-keys")
out = []
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        with open(p, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if PAT.search(line):
                    out.append(f"{os.path.relpath(p, ROOT)}:{i}:{line.rstrip()}")
with open(r"D:\ai大模型\DRAGON-AI-master\site\_be_test_eps.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
print("N=" + str(len(out)))
