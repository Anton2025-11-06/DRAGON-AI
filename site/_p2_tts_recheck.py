# -*- coding: utf-8 -*-
"""Phase 2 复验：仅重跑修复后的 text_to_audio x zhipu（二进制音频 -> data URI）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _p2_workflow_test import run_one  # noqa: E402


async def main():
    r = await run_one("text_to_audio", "zhipu", "glm-tts")
    out = r.get("out") or {}
    audio = out.get("audio_url") or ""
    print("status:", r["status"], "keys:", r["keys"])
    print("audio_url head:", str(audio)[:60])
    print("is_data_uri:", audio.startswith("data:audio/"))


if __name__ == "__main__":
    asyncio.run(main())
