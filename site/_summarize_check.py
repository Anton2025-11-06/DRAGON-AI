# -*- coding: utf-8 -*-
"""验证测试按钮摘要与 data 结构：_summarize 各分支 + test_model 真实调用（快组合）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_httpx.httpx import httpx_pool
httpx_pool.init()

from common.common_model.base import ModelResult
from common.common_model.entry import _summarize
from common.common_model.model_types import ModelConfig

# ---- 1. _summarize 纯函数分支 ----
print('TEXT :', _summarize('text_to_text', ModelResult(content='哈' * 100)))
print('VEC  :', _summarize('text_embedding', ModelResult(vectors=[[0.123456, -0.005, 1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]])))
print('RR   :', _summarize('text_rerank', ModelResult(scores=[{'index': 0, 'relevance_score': 0.9523}, {'index': 1, 'relevance_score': 0.3111}])))
long_url = 'https://dashscope.oss-cn-beijing.aliyuncs.com/images/very/long/full/path/example_with_many_segments_and_filename.jpeg?x-oss-process=image/resize,w_800'
print('URL  :', _summarize('text_to_image', ModelResult(url=long_url)))
print('BYTES:', _summarize('text_to_audio', ModelResult(audio_bytes=b'x' * 1024)))

# ---- 2. test_model 真实调用（3 个快组合，验证 data 结构化字段）----
from common.common_model.entry import test_model

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
ZB = "https://open.bigmodel.cn/api/paas/v4/"
QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def cfg(cat, provider, base, key, model_name):
    return ModelConfig(model_id=0, name='t', category=cat, provider=provider,
                       model_name=model_name, base_url=base, api_key=key)


CASES = [
    ('text_to_text', 'dashscope', QB, QWEN_KEY, 'qwen-plus'),
    ('text_embedding', 'dashscope', QB, QWEN_KEY, 'text-embedding-v3'),
    ('text_rerank', 'dashscope', QB, QWEN_KEY, 'gte-rerank-v2'),
    ('text_to_audio', 'zhipu', ZB, ZHIPU_KEY, 'glm-tts'),
]


async def main():
    for cat, provider, base, key, model_name in CASES:
        r = await test_model(cat, cfg(cat, provider, base, key, model_name), with_stream=True)
        d = r.get('data') or {}
        print(f"\n[{cat} x {provider}] success={r['success']}")
        print('  message:', r['message'][:160])
        print('  data keys:', sorted(d.keys()))
        for k in ('content', 'urls', 'vectors', 'scores', 'audio_bytes_len'):
            if d.get(k) is not None:
                v = str(d[k])
                print(f'  {k}={v[:180]}{"..." if len(v) > 180 else ""}')


asyncio.run(main())