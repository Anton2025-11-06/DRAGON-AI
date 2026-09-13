import os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pnpm = os.path.join(root, "ui-ai", "node_modules", ".pnpm")
found = []
if os.path.isdir(pnpm):
    for name in os.listdir(pnpm):
        if "cssnano" in name.lower():
            found.append(name)
print("CSSNANO_IN_PNPM:", found if found else "NONE")
# 检查 @vben/tailwind-config 的 postcss 配置引用了哪些插件
tc = os.path.join(root, "ui-ai", "internal", "tailwind-config")
for dirpath, dirs, files in os.walk(tc):
    if "node_modules" in dirpath:
        continue
    for fn in files:
        if "postcss" in fn.lower():
            fp = os.path.join(dirpath, fn)
            print("----", os.path.relpath(fp, root))
            try:
                print(open(fp, "r", encoding="utf-8").read()[:600])
            except Exception as e:
                print("read err", e)
