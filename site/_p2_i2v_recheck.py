# -*- coding: utf-8 -*-
"""单项复验：image_to_video x dashscope（判断全量回归 EXC 是否为服务端偶发）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _p2_workflow_test import run_one  # noqa: E402


async def main():
    r = await run_one("image_to_video", "dashscope", "wanx2.1-i2v-turbo")
    print("status:", r["status"], "ms:", r["ms"], "keys:", r["keys"])
    print("detail:", r.get("detail"))


if __name__ == "__main__":
    asyncio.run(main())