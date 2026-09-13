# -*- coding: utf-8 -*-
"""纵向切片验证：通过 common_model 体系实调 text_to_text（智谱/通义）非流式+流式+thinking。"""
import asyncio

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_model import matrix, providers_for, categories_for, instantiate
from common.common_model.model_types import ModelConfig

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"


def cfg(provider, base, key, model):
    return ModelConfig(model_id=1, provider=provider, model_name=model,
                       base_url=base, api_key=key)


async def run_one(name, config):
    print(f"\n--- {name} ---")
    inst = instantiate("text_to_text", config)
    # 非流式
    r = await inst.ainvoke(prompt="说一个字")
    print("ainvoke:", repr(r.content[:20]), "usage=", r.usage)
    parsed = await inst.aparse(r)
    print("aparse:", repr(parsed[:20]))
    # 流式
    buf = ""
    async for c in inst.astream(prompt="说一个字"):
        buf += c.content
    print("astream:", repr(buf[:20]))
    # thinking（推理模型才有；普通模型可能无 reasoning）
    try:
        rt = await inst.ainvoke(prompt="1+1=? 推理", thinking=True)
        print("thinking: reasoning=", repr((rt.reasoning_content or "")[:20]))
    except Exception as e:
        print("thinking ERR:", type(e).__name__, str(e)[:120])


async def main():
    print("providers_for(text_to_text) =", providers_for("text_to_text"))
    print("categories_for(zhipu) =", categories_for("zhipu"))
    print("matrix =", matrix())
    await run_one("智谱 zhipu", instantiate("text_to_text", cfg(
        "zhipu", "https://open.bigmodel.cn/api/paas/v4/", ZHIPU_KEY, "glm-4-flash")).config)
    await run_one("通义 dashscope", cfg(
        "dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1", QWEN_KEY, "qwen-turbo"))


if __name__ == "__main__":
    asyncio.run(main())
