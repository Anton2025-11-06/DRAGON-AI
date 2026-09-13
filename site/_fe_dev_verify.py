# -*- coding: utf-8 -*-
"""通过 vite dev server 请求各改造模块，验证 SFC/TS 转译无错误。

vite dev 会对每个源模块做按需转译：模板编译错误 / import 解析失败 会以非 200 或
返回包含 error 的模块体现。这里逐个 GET 并检查。
"""
import urllib.request
import urllib.error

BASE = "http://localhost:5999"
MODULES = [
    "/",  # index.html
    "/src/api/ai-workflow/const.ts",
    "/src/views/wemirr/ai/model-plaza/api.ts",
    "/src/views/wemirr/ai/model-plaza/components/CreateModelDialog.vue",
    "/src/views/wemirr/ai/workflow/components/model-select/ModelSelect.vue",
    "/src/views/wemirr/ai/workflow/components/node-forms/LLMNodeForm.vue",
    "/src/views/wemirr/ai/workflow/components/node-forms/QuestionClassifierNodeForm.vue",
    "/src/views/wemirr/ai/workflow/components/node-forms/ParameterExtractorNodeForm.vue",
    "/src/views/wemirr/ai/workflow/components/node-forms/KnowledgeNodeForm.vue",
    "/src/views/wemirr/ai/model-plaza/chat/api.ts",
    "/src/views/wemirr/ai/model-plaza/chat/index.vue",
    "/src/views/wemirr/ai/model-plaza/chat/components/ChatInput.vue",
    "/src/views/wemirr/ai/model-plaza/index.vue",
]


def fetch(path):
    url = BASE + path
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


lines = []
all_ok = True
for m in MODULES:
    status, body = fetch(m)
    # vite 转译失败：HTTP 5xx 或 body 含明显错误标记
    bad_markers = [t for t in ["Transform failed", "Failed to resolve import",
                               "[plugin:vite:", "Internal server error",
                               "Pre-transform error", "Parse error"] if t in body]
    ok = status == 200 and not bad_markers
    all_ok = all_ok and ok
    mark = "OK " if ok else "ERR"
    lines.append(f"[{mark}] {status:3d} {m}  bytes={len(body)}  err={bad_markers}")
    if not ok:
        # 打印错误正文片段
        lines.append("      >>> " + body[:400].replace("\n", " "))

report = "\n".join(lines)
with open("site/_fe_dev_verify.txt", "w", encoding="utf-8") as f:
    f.write(report + f"\n\nALL_OK={all_ok}\n")
print(report)
print("ALL_OK=", all_ok)
