# -*- coding: utf-8 -*-
"""entry.py 冒烟：验证导入 + test_model（含 stream）+ body/ result 适配器（真实调用智谱文生文）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_model import entry
from common.common_model.model_types import ModelConfig

ZB = "https://open.bigmodel.cn/api/paas/v4/"
ZK = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"


async def main():
    cfg = ModelConfig(model_id=1, category="text_to_text", provider="zhipu",
                      model_name="glm-4-flash", base_url=ZB, api_key=ZK)
    # 1) 测试按钮核心
    res = await entry.test_model("text_to_text", cfg)
    print("test_model:", res)
    # 2) 网关 body → kwargs → 调用 → result → openai
    kw = entry.body_to_kwargs("text_to_text", {"messages": [{"role": "user", "content": "说一个字"}]})
    inst = entry.instantiate("text_to_text", cfg)
    r = await inst.ainvoke(**kw)
    print("result_to_openai:", entry.result_to_openai("text_to_text", "glm-4-flash", r))
    # 3) SSE 封装
    n = 0
    async for line in entry.stream_to_openai_sse("text_to_text", "glm-4-flash",
                                                 inst.astream(**kw)):
        n += 1
        if n <= 2:
            print("sse:", line.strip()[:80])
    print("sse frames:", n)
    # 4) config_from_row（dict 与 ORM 两种来源）
    print("supports zhipu/image_embedding:", entry.supports("image_embedding", "zhipu"))


if __name__ == "__main__":
    asyncio.run(main())
