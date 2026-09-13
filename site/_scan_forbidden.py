# -*- coding: utf-8 -*-
"""全仓扫描：确认「直连/非直连 + 接口后缀」概念已彻底删除。"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"venv", "node_modules", ".git", "dist", "__pycache__", ".idea", "site"}
PATTERNS = [
    r"\bis_direct\b", r"\bnot\s*direct\b", r"\bsuffixes\b", r"\bsuffix_url\b",
    r"\bmodelIsDirect\b", r"\bsuffixValue\b", r"\bModelSuffixOption\b",
    r"\buse_suffix\b", r"\bsuffix_id\b", r"\bsuffixOptions\b",
]
# antd 图标 suffixIcon 属组件库正常用法，非本概念，排除
EXCLUDE = re.compile(r"suffixIcon")
# 旧库迁移 DDL：DROP COLUMN / COLUMN_NAME 存在性检查必须引用历史列名，属功能性幂等脚本，不算残留
EXCLUDE_DDL = re.compile(r"DROP\s+COLUMN\s*`?(is_direct|suffixes)`?|COLUMN_NAME\s*=\s*'(is_direct|suffixes)'")
rx = [re.compile(p) for p in PATTERNS]

hits = []
for dirpath, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        if not fn.endswith((".py", ".ts", ".vue", ".tsx", ".sql", ".js")):
            continue
        fp = os.path.join(dirpath, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if EXCLUDE.search(line) or EXCLUDE_DDL.search(line):
                        continue
                    for p in rx:
                        if p.search(line):
                            hits.append(f"{os.path.relpath(fp, ROOT)}:{i}: {line.strip()[:120]}")
                            break
        except (UnicodeDecodeError, OSError):
            pass

with open(os.path.join(ROOT, "site", "_scan_forbidden.txt"), "w", encoding="utf-8") as f:
    if hits:
        f.write(f"共 {len(hits)} 处残留:\n")
        f.write("\n".join(hits))
    else:
        f.write("CLEAN: 未发现任何 is_direct/suffixes/modelIsDirect/suffix_url 等残留\n")
print(f"hits={len(hits)}")
for h in hits[:80]:
    print(h)
