# -*- coding: utf-8 -*-
"""模型标识注册表候选验证：逐 (provider, category, model_name) 用真实 API 调用测试。

通过（PASS）的标识才有资格写入 model_registry 注册表 —— 「必须亲自测试验证过」。

用法：python site/_reg_verify.py [fast|gen|all]
  fast = 同步/低成本（文生文/向量/重排/图片向量/图片理解/视频理解/OCR/TTS/ASR）
  gen  = 生成类异步任务（文生图/文生视频/图生视频），耗时长
  all  = 全部（默认）
输出：site/_reg_verify_result.json（通过清单，注册表数据源）+ 控制台逐条结果
说明：openai=通用 OpenAI 兼容客户端（本环境无 api.openai.com，指向通义兼容端点验证调用路径）。
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_httpx.httpx import httpx_pool
from common.common_model import instantiate
from common.common_model.model_types import ModelConfig

# 与 FastAPI 应用同样初始化 httpx 连接池（否则 http_pool.client 为 None，
# rerank/图片向量/TTS/ASR 等原生端点路径直接 AttributeError）
httpx_pool.init(timeout=120)

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
ZB = "https://open.bigmodel.cn/api/paas/v4/"
QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"
IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
AUDIO = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"
VIDEO = "https://media.w3.org/2010/05/sintel/trailer.mp4"

# ==================== 候选清单（每 (类型,厂家) 多个官方文档模型名，逐个实测） ====================
FAST = {
    "text_to_text": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-flash", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-flash-250414", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-air", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-airx", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-plus", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-long", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4-0520", {"prompt": "说一个字"}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4.5-flash", {"prompt": "说一个字"}),
        ("dashscope", QB, QWEN_KEY, "qwen-turbo", {"prompt": "说一个字"}),
        ("dashscope", QB, QWEN_KEY, "qwen-plus", {"prompt": "说一个字"}),
        ("dashscope", QB, QWEN_KEY, "qwen-max", {"prompt": "说一个字"}),
        ("dashscope", QB, QWEN_KEY, "qwen-flash", {"prompt": "说一个字"}),
        ("dashscope", QB, QWEN_KEY, "qwen-long", {"prompt": "说一个字"}),
        ("openai", QB, QWEN_KEY, "qwen-turbo", {"prompt": "说一个字"}),
        ("openai", QB, QWEN_KEY, "qwen-plus", {"prompt": "说一个字"}),
        ("openai", QB, QWEN_KEY, "qwen-max", {"prompt": "说一个字"}),
        ("openai", QB, QWEN_KEY, "qwen-flash", {"prompt": "说一个字"}),
    ],
    "text_embedding": [
        ("zhipu", ZB, ZHIPU_KEY, "embedding-3", {"input": "你好"}),
        ("zhipu", ZB, ZHIPU_KEY, "embedding-3-light", {"input": "你好"}),
        ("dashscope", QB, QWEN_KEY, "text-embedding-v3", {"input": "你好"}),
        ("dashscope", QB, QWEN_KEY, "text-embedding-v4", {"input": "你好"}),
        ("dashscope", QB, QWEN_KEY, "text-embedding-v2", {"input": "你好"}),
        ("openai", QB, QWEN_KEY, "text-embedding-v3", {"input": "你好"}),
    ],
    "text_rerank": [
        ("zhipu", ZB, ZHIPU_KEY, "rerank", {"query": "苹果", "documents": ["苹果手机", "香蕉"]}),
        ("zhipu", ZB, ZHIPU_KEY, "bge-reranker-v2-m3", {"query": "苹果", "documents": ["苹果手机", "香蕉"]}),
        ("dashscope", QB, QWEN_KEY, "gte-rerank-v2", {"query": "苹果", "documents": ["苹果手机", "香蕉"]}),
        ("dashscope", QB, QWEN_KEY, "gte-rerank", {"query": "苹果", "documents": ["苹果手机", "香蕉"]}),
    ],
    "image_embedding": [
        ("dashscope", QB, QWEN_KEY, "multimodal-embedding-v1", {"image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "multimodal-embedding-v3", {"image_url": IMG}),
    ],
    "image_understand": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-flash", {"prompt": "图里有啥", "image_url": IMG}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-plus", {"prompt": "图里有啥", "image_url": IMG}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-9b", {"prompt": "图里有啥", "image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "图里有啥", "image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-max", {"prompt": "图里有啥", "image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "qwen2.5-vl-72b-instruct", {"prompt": "图里有啥", "image_url": IMG}),
        ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "图里有啥", "image_url": IMG}),
        ("openai", QB, QWEN_KEY, "qwen-vl-max", {"prompt": "图里有啥", "image_url": IMG}),
    ],
    "video_understand": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-plus", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-flash", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-max", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("dashscope", QB, QWEN_KEY, "qwen2.5-vl-72b-instruct", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "视频讲了啥", "video_url": VIDEO}),
        ("openai", QB, QWEN_KEY, "qwen-vl-max", {"prompt": "视频讲了啥", "video_url": VIDEO}),
    ],
    "ocr": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-flash", {"image_url": IMG}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-4v-plus", {"image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-ocr", {"image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "qwen-vl-ocr-250828", {"image_url": IMG}),
        ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"image_url": IMG}),
    ],
    "text_to_audio": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-tts", {"text": "你好"}),
        ("dashscope", QB, QWEN_KEY, "qwen-tts", {"text": "你好"}),
        ("dashscope", QB, QWEN_KEY, "cosyvoice-v1", {"text": "你好"}),
        ("openai", QB, QWEN_KEY, "qwen-tts", {"text": "你好"}),
    ],
    "audio_to_text": [
        ("zhipu", ZB, ZHIPU_KEY, "glm-asr", {"audio_url": AUDIO}),
        ("zhipu", ZB, ZHIPU_KEY, "glm-asr-plus", {"audio_url": AUDIO}),
        ("dashscope", QB, QWEN_KEY, "paraformer-v2", {"audio_url": AUDIO}),
        ("dashscope", QB, QWEN_KEY, "paraformer-v3", {"audio_url": AUDIO}),
        ("dashscope", QB, QWEN_KEY, "sensevoice-v1", {"audio_url": AUDIO}),
        ("openai", QB, QWEN_KEY, "paraformer-v2", {"audio_url": AUDIO}),
    ],
}

# 生成类（真实生成，耗时/计费；用户明确要求每种标识都要实测）
GEN = {
    "text_to_image": [
        ("zhipu", ZB, ZHIPU_KEY, "cogview-3-flash", {"prompt": "一只戴帽子的猫"}),
        ("zhipu", ZB, ZHIPU_KEY, "cogview-3", {"prompt": "一只戴帽子的猫"}),
        ("zhipu", ZB, ZHIPU_KEY, "cogview-3-plus", {"prompt": "一只戴帽子的猫"}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-t2i-turbo", {"prompt": "一只戴帽子的猫"}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-t2i-plus", {"prompt": "一只戴帽子的猫"}),
    ],
    "text_to_video": [
        ("zhipu", ZB, ZHIPU_KEY, "cogvideox-flash", {"prompt": "一只猫走路"}),
        ("zhipu", ZB, ZHIPU_KEY, "cogvideox", {"prompt": "一只猫走路"}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-t2v-turbo", {"prompt": "一只猫走路"}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-t2v-plus", {"prompt": "一只猫走路"}),
    ],
    "image_to_video": [
        ("zhipu", ZB, ZHIPU_KEY, "cogvideox-flash", {"image_url": IMG}),
        ("zhipu", ZB, ZHIPU_KEY, "cogvideox", {"image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-i2v-turbo", {"image_url": IMG}),
        ("dashscope", QB, QWEN_KEY, "wanx2.1-i2v-plus", {"image_url": IMG}),
    ],
}

STREAM_TYPES = {"text_to_text", "image_understand", "video_understand"}


async def test_one(cat, provider, base, key, model, kw):
    cfg = ModelConfig(model_id=1, provider=provider, model_name=model, base_url=base, api_key=key)
    inst = instantiate(cat, cfg)
    t0 = time.monotonic()
    try:
        r = await inst.ainvoke(**kw)
        ms = int((time.monotonic() - t0) * 1000)
        if r.scores is not None:
            brief = f"scores={r.scores[:1]}"
        elif r.audio_bytes is not None:
            brief = f"audio_bytes={len(r.audio_bytes)}"
        elif r.url or r.urls:
            brief = f"url={(r.url or (r.urls or [''])[0])[:60]}"
        elif r.vectors is not None:
            brief = f"vectors[0][:3]={r.vectors[0][:3]} dim={len(r.vectors[0])}"
        else:
            brief = f"content={r.content[:24]!r}"
        smsg = ""
        if cat in STREAM_TYPES:
            n = 0
            async for _ in inst.astream(**kw):
                n += 1
            smsg = f" | stream_chunks={n}"
        print(f"[PASS] {cat:16s} {provider:10s} {model:28s} {ms:6d}ms  {brief}{smsg}", flush=True)
        return (cat, provider, model, True, brief)
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        print(f"[FAIL] {cat:16s} {provider:10s} {model:28s} {ms:6d}ms  {type(e).__name__}: {str(e)[:110]}", flush=True)
        return (cat, provider, model, False, f"{type(e).__name__}: {str(e)[:150]}")


async def run_group(group, label, concurrency):
    print(f"\n########## {label} ##########", flush=True)
    cases = []
    for cat, lst in group.items():
        for t in lst:
            cases.append(test_one(cat, *t))
    sem = asyncio.Semaphore(concurrency)

    async def guard(coro):
        async with sem:
            return await coro
    return await asyncio.gather(*[guard(c) for c in cases])


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = []
    if mode in ("fast", "all"):
        results += await run_group(FAST, "FAST 同步/低成本", concurrency=6)
    if mode in ("gen", "all"):
        results += await run_group(GEN, "GEN 生成类异步(耗时)", concurrency=2)
    passed = [r for r in results if r[3]]
    failed = [r for r in results if not r[3]]
    print(f"\n===== 汇总 mode={mode}: {len(passed)}/{len(results)} 通过 =====", flush=True)
    for r in failed:
        print("  FAIL:", r[0], r[1], r[2], "->", r[4], flush=True)
    # 通过清单落盘（注册表数据源）：与既有结果合并，避免多次分批运行互相覆盖
    registry = {}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_reg_verify_result.json")
    if os.path.exists(out):
        try:
            with open(out, "r", encoding="utf-8") as f:
                registry = json.load(f).get("passed", {}) or {}
        except Exception:
            registry = {}
    for cat, provider, model, ok, brief in passed:
        lst = registry.setdefault(provider, {}).setdefault(cat, [])
        if model not in lst:
            lst.append(model)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "passed": registry, "failed": [list(r[:4]) for r in failed]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n通过清单已写入 {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())