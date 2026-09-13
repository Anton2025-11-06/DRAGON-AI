# -*- coding: utf-8 -*-
"""common_model 全量真实测试矩阵：逐 (类型, 供应商) 注册组合，通过真实类栈调用。

用法：python site/_cm_full_test.py [fast|gen|all]
  fast = 同步/低成本类型（文生文/向量/重排/图片向量/图片理解/视频理解/OCR/TTS/ASR）
  gen  = 生成类异步任务（文生图/文生视频/图生视频），耗时长
  all  = 全部
说明：openai 供应商=通用 OpenAI 兼容客户端，本环境无 api.openai.com，
      指向通义 compatible-mode 端点以验证「通用客户端」调用路径（仅 chat/embeddings 族可达）。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_model import instantiate, providers_for
from common.common_model.model_types import ModelConfig

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
ZB = "https://open.bigmodel.cn/api/paas/v4/"
QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"
IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
AUDIO = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"
VIDEO = "https://media.w3.org/2010/05/sintel/trailer.mp4"

FAST = {
    "text_to_text":      [("zhipu", ZB, ZHIPU_KEY, "glm-4-flash", {"prompt": "说一个字"}),
                          ("dashscope", QB, QWEN_KEY, "qwen-turbo", {"prompt": "说一个字"}),
                          ("openai", QB, QWEN_KEY, "qwen-turbo", {"prompt": "说一个字"})],
    "text_embedding":    [("zhipu", ZB, ZHIPU_KEY, "embedding-3", {"input": "你好"}),
                          ("dashscope", QB, QWEN_KEY, "text-embedding-v3", {"input": "你好"}),
                          ("openai", QB, QWEN_KEY, "text-embedding-v3", {"input": "你好"})],
    "text_rerank":       [("zhipu", ZB, ZHIPU_KEY, "rerank", {"query": "苹果", "documents": ["苹果手机", "香蕉"]}),
                          ("dashscope", QB, QWEN_KEY, "gte-rerank-v2", {"query": "苹果", "documents": ["苹果手机", "香蕉"]})],
    "image_embedding":   [("dashscope", QB, QWEN_KEY, "multimodal-embedding-v1", {"image_url": IMG})],
    "image_understand":  [("zhipu", ZB, ZHIPU_KEY, "glm-4v-flash", {"prompt": "图里有啥", "image_url": IMG}),
                          ("dashscope", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "图里有啥", "image_url": IMG}),
                          ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "图里有啥", "image_url": IMG})],
    "video_understand":  [("zhipu", ZB, ZHIPU_KEY, "glm-4v-plus", {"prompt": "视频讲了啥", "video_url": VIDEO}),
                          ("dashscope", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "视频讲了啥", "video_url": VIDEO}),
                          ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"prompt": "视频讲了啥", "video_url": VIDEO})],
    "ocr":               [("zhipu", ZB, ZHIPU_KEY, "glm-4v-flash", {"image_url": IMG}),
                          ("dashscope", QB, QWEN_KEY, "qwen-vl-ocr", {"image_url": IMG}),
                          ("openai", QB, QWEN_KEY, "qwen-vl-plus", {"image_url": IMG})],
    "text_to_audio":     [("zhipu", ZB, ZHIPU_KEY, "glm-tts", {"text": "你好"}),
                          ("dashscope", QB, QWEN_KEY, "qwen-tts", {"text": "你好"})],
    "audio_to_text":     [("zhipu", ZB, ZHIPU_KEY, "glm-asr", {"audio_url": AUDIO}),
                          ("dashscope", QB, QWEN_KEY, "paraformer-v2", {"audio_url": AUDIO})],
}

GEN = {
    # 智谱 cogview / 通义 wanx 均原生支持文生图，真实生成可校验。
    # openai 的 /images/generations 已注册(真实 OpenAI 支持 dall-e)，但本环境无可达的
    # OpenAI 图像端点(dashscope compatible-mode 不提供 images)，故 live 仅测原生两家。
    "text_to_image":     [("zhipu", ZB, ZHIPU_KEY, "cogview-3-flash", {"prompt": "一只猫"}),
                          ("dashscope", QB, QWEN_KEY, "wanx2.1-t2i-turbo", {"prompt": "一只猫"})],
    # 视频生成仅两家原生支持(智谱 cogvideox / 通义 wanx)，openai 未注册该类型。
    "text_to_video":     [("zhipu", ZB, ZHIPU_KEY, "cogvideox-flash", {"prompt": "一只猫走路"}),
                          ("dashscope", QB, QWEN_KEY, "wanx2.1-t2v-turbo", {"prompt": "一只猫"})],
    "image_to_video":    [("zhipu", ZB, ZHIPU_KEY, "cogvideox-flash", {"image_url": IMG}),
                          ("dashscope", QB, QWEN_KEY, "wanx2.1-i2v-turbo", {"image_url": IMG})],
}

STREAM_TYPES = {"text_to_text", "image_understand", "video_understand"}


async def test_one(cat, provider, base, key, model, kw):
    cfg = ModelConfig(model_id=1, provider=provider, model_name=model, base_url=base, api_key=key)
    inst = instantiate(cat, cfg)
    t0 = time.monotonic()
    try:
        r = await inst.ainvoke(**kw)
        ms = int((time.monotonic() - t0) * 1000)
        # 关键字段摘要
        if r.vectors is not None:
            brief = f"vectors[0][:3]={r.vectors[0][:3]} dim={len(r.vectors[0])}"
        elif r.scores is not None:
            brief = f"scores={r.scores[:1]}"
        elif r.audio_bytes is not None:
            brief = f"audio_bytes={len(r.audio_bytes)}"
        elif r.url or r.urls:
            brief = f"url={r.url or (r.urls or [''])[0][:50]}"
        else:
            brief = f"content={r.content[:24]!r}"
        # 流式类型额外测 stream
        smsg = ""
        if cat in STREAM_TYPES:
            n = 0
            async for _ in inst.astream(**{k: v for k, v in kw.items()}):
                n += 1
            smsg = f" | stream_chunks={n}"
        print(f"[PASS] {cat:16s} {provider:10s} {model:26s} {ms:6d}ms  {brief}{smsg}")
        return (cat, provider, model, True, f"{ms}ms {brief}{smsg}")
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        print(f"[FAIL] {cat:16s} {provider:10s} {model:26s} {ms:6d}ms  {type(e).__name__}: {str(e)[:120]}")
        return (cat, provider, model, False, f"{type(e).__name__}: {str(e)[:160]}")


async def run_group(group, label, concurrency):
    print(f"\n########## {label} ##########")
    cases = []
    for cat, lst in group.items():
        for (provider, base, key, model, kw) in lst:
            cases.append(test_one(cat, provider, base, key, model, kw))
    sem = asyncio.Semaphore(concurrency)

    async def guard(coro):
        async with sem:
            return await coro
    results = await asyncio.gather(*[guard(c) for c in cases])
    return results


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    results = []
    if mode in ("fast", "all"):
        results += await run_group(FAST, "FAST 同步/低成本", concurrency=6)
    if mode in ("gen", "all"):
        results += await run_group(GEN, "GEN 生成类异步(耗时)", concurrency=2)
    passed = sum(1 for r in results if r[3])
    print(f"\n===== 汇总 mode={mode}: {passed}/{len(results)} 通过 =====")
    for r in results:
        if not r[3]:
            print("  FAIL:", r[0], r[1], r[2], "->", r[4])


if __name__ == "__main__":
    asyncio.run(main())
