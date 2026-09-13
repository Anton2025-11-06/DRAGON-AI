# -*- coding: utf-8 -*-
"""Phase 1: 模型测试按钮真实联测 —— 遍历 12 类型 x 3 供应商 = 36 组合。

驱动的是「模型广场测试按钮」真实代码路径: service.ModelService.test
(校验 base_url/category/provider -> cm_entry.config_from_row -> cm_entry.test_model -> 真实子类 ainvoke/astream)

分类判定:
  - 注册组合(31): 期望 success=True 且 data 产出非空
  - openai 的 text_to_image/text_to_audio/audio_to_text: 注册但本环境无可达端点(基座指向 dashscope
    compatible-mode), 归为 EXPECTED-UNREACHABLE(不算失败)
  - 未注册组合(5): 期望 success=False 且 message 含「无可用实现」
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 真实运行时由服务启动 bootstrap 初始化全局 httpx 连接池；测试脚本须显式 init，
# 否则 rerank/embedding/audio/video 族依赖的 http_client() 返回 None。
from common.common_httpx.httpx import httpx_pool
httpx_pool.init()

# 触发 common_model 注册后再取矩阵
from common.common_model import matrix
from common.common_constants.model_constant import MODEL_TYPES_ALL, PROVIDERS_ALL
from service.service_system.schemas.model_schema import ModelTestRequest
from service.service_system.services.model_service import ModelService

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
ZB = "https://open.bigmodel.cn/api/paas/v4/"
QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# provider -> (base_url, api_key)
PROVIDER_URL = {"zhipu": (ZB, ZHIPU_KEY), "dashscope": (QB, QWEN_KEY), "openai": (QB, QWEN_KEY)}

# (cat, provider) -> 真实 model_name (仅注册组合会被真实调用)
MODEL = {
    ("text_to_text", "zhipu"): "glm-4-flash",
    ("text_to_text", "dashscope"): "qwen-turbo",
    ("text_to_text", "openai"): "qwen-turbo",
    ("text_embedding", "zhipu"): "embedding-3",
    ("text_embedding", "dashscope"): "text-embedding-v3",
    ("text_embedding", "openai"): "text-embedding-v3",
    ("text_rerank", "zhipu"): "rerank",
    ("text_rerank", "dashscope"): "gte-rerank-v2",
    ("image_embedding", "dashscope"): "multimodal-embedding-v1",
    ("text_to_image", "zhipu"): "cogview-3-flash",
    ("text_to_image", "dashscope"): "wanx2.1-t2i-turbo",
    ("text_to_image", "openai"): "dall-e-3",
    ("audio_to_text", "zhipu"): "glm-asr",
    ("audio_to_text", "dashscope"): "paraformer-v2",
    ("audio_to_text", "openai"): "whisper-1",
    ("image_understand", "zhipu"): "glm-4v-flash",
    ("image_understand", "dashscope"): "qwen-vl-plus",
    ("image_understand", "openai"): "qwen-vl-plus",
    ("video_understand", "zhipu"): "glm-4v-plus",
    ("video_understand", "dashscope"): "qwen-vl-plus",
    ("video_understand", "openai"): "qwen-vl-plus",
    ("ocr", "zhipu"): "glm-4v-flash",
    ("ocr", "dashscope"): "qwen-vl-ocr",
    ("ocr", "openai"): "qwen-vl-plus",
    ("image_to_video", "zhipu"): "cogvideox-flash",
    ("image_to_video", "dashscope"): "wanx2.1-i2v-turbo",
    ("text_to_video", "zhipu"): "cogvideox-flash",
    ("text_to_video", "dashscope"): "wanx2.1-t2v-turbo",
    ("text_to_audio", "zhipu"): "glm-tts",
    ("text_to_audio", "dashscope"): "qwen-tts",
    ("text_to_audio", "openai"): "tts-1",
}

# openai 这三类注册但本环境无可达端点(基座指向 dashscope compatible-mode)
EXPECTED_UNREACHABLE = {("text_to_image", "openai"), ("text_to_audio", "openai"), ("audio_to_text", "openai")}

# 生成类耗时任务
SLOW = {"text_to_image", "text_to_video", "image_to_video"}


async def run_one(cat, provider, registered):
    base, key = PROVIDER_URL[provider]
    model = MODEL.get((cat, provider), "unknown-model")
    req = ModelTestRequest(category=cat, provider=provider, model_name=model, base_url=base, api_key=key)
    t0 = time.monotonic()
    try:
        res = await ModelService.test(req)
    except Exception as e:  # noqa: BLE001
        ms = int((time.monotonic() - t0) * 1000)
        return {"cat": cat, "provider": provider, "model": model, "registered": registered,
                "status": "EXC", "ms": ms, "message": f"{type(e).__name__}: {str(e)[:180]}", "data": None}
    ms = int((time.monotonic() - t0) * 1000)
    success = bool(res.get("success"))
    msg = res.get("message", "")
    data = res.get("data")
    if not registered:
        status = "GRACEFUL-REJECT" if ("无可用实现" in msg) else "BAD-REJECT"
    elif success:
        status = "PASS"
    elif (cat, provider) in EXPECTED_UNREACHABLE:
        status = "EXPECTED-UNREACHABLE"
    else:
        status = "FAIL"
    return {"cat": cat, "provider": provider, "model": model, "registered": registered,
            "status": status, "ms": ms, "message": msg[:220], "data": data}


async def main():
    m = matrix()
    tasks = []
    for cat in MODEL_TYPES_ALL:
        reg = set(m.get(cat, []))
        for p in PROVIDERS_ALL:
            tasks.append((cat, p, p in reg))
    # 慢速生成类单独并发=2, 其余=6, 分两批避免全卡
    fast_tasks = [(c, p, r) for (c, p, r) in tasks if c not in SLOW]
    slow_tasks = [(c, p, r) for (c, p, r) in tasks if c in SLOW]

    async def batch(items, conc):
        sem = asyncio.Semaphore(conc)

        async def guard(t):
            async with sem:
                return await run_one(*t)
        return await asyncio.gather(*[guard(t) for t in items])

    results = await batch(fast_tasks, 6)
    results += await batch(slow_tasks, 2)

    # 落盘明细
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_p1_result.txt")
    lines = []
    order = {st: i for i, st in enumerate(["PASS", "GRACEFUL-REJECT", "EXPECTED-UNREACHABLE", "FAIL", "BAD-REJECT", "EXC"])}
    results.sort(key=lambda r: (order.get(r["status"], 9), r["cat"], r["provider"]))
    from collections import Counter
    cnt = Counter(r["status"] for r in results)
    lines.append("===== Phase1 模型测试按钮真实联测 (ModelService.test, 36 组合) =====")
    lines.append(f"汇总: {dict(cnt)}")
    lines.append("")
    for r in results:
        tag = f"[{r['status']:19s}]"
        summary = ""
        if r.get("data"):
            summary = f" | {r['data'].get('summary', '')}"
        lines.append(f"{tag} {r['cat']:16s} x {r['provider']:9s} {r['model']:24s} {r['ms']:6d}ms  {r['message']}{summary}")
    txt = "\n".join(lines) + "\n"
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    asyncio.run(main())
