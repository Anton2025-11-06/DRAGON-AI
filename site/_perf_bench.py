# -*- coding: utf-8 -*-
"""性能基准：对比「AsyncOpenAI SDK」与「httpx 连接池直连」两种接入方式的并发吞吐/时延。

结论用于指导 common_model 子类的「性能最佳接入方式」选型（用户要求：一切以实测性能为准，
sdk 仅限厂家原生 sdk 或 openai sdk，否则走 http_pool）。

测法：同一 OpenAI 兼容 chat 端点，相同 prompt，并发 N 个 ainvoke 请求，分别用两种方式跑，
统计总墙钟耗时、QPS、平均/P95 单请求时延。为控制成本用极短 prompt + 小 max_tokens。

用法：python site/_perf_bench.py [并发数, 默认20] [轮次, 默认2]
"""
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from openai import AsyncOpenAI

QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
MODEL = "qwen-turbo"
MSG = [{"role": "user", "content": "说一个字"}]


def _pct(vals, p):
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1)))))
    return vals[k]


async def bench_sdk(concurrency: int, client: AsyncOpenAI):
    """方式A：AsyncOpenAI SDK（内部 httpx 连接池复用）。"""
    lat = []

    async def one():
        t0 = time.monotonic()
        await client.chat.completions.create(model=MODEL, messages=MSG, max_tokens=6)
        lat.append((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    await asyncio.gather(*[one() for _ in range(concurrency)])
    wall = time.monotonic() - t0
    return wall, lat


async def bench_http(concurrency: int, pool: httpx.AsyncClient):
    """方式B：httpx 连接池直连 /chat/completions（无 SDK 封装开销）。"""
    url = QB.rstrip("/") + "/chat/completions"
    hdr = {"Authorization": f"Bearer {QWEN_KEY}"}
    body = {"model": MODEL, "messages": MSG, "max_tokens": 6}
    lat = []

    async def one():
        t0 = time.monotonic()
        r = await pool.post(url, headers=hdr, json=body)
        r.raise_for_status()
        lat.append((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    await asyncio.gather(*[one() for _ in range(concurrency)])
    wall = time.monotonic() - t0
    return wall, lat


async def main():
    concurrency = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    # 连接池按最佳实践配置（keep-alive 复用，避免每请求新建 TLS）
    sdk = AsyncOpenAI(base_url=QB, api_key=QWEN_KEY, timeout=120.0, max_retries=0)
    pool = httpx.AsyncClient(timeout=120.0, trust_env=False,
                             limits=httpx.Limits(max_connections=concurrency + 20,
                                                 max_keepalive_connections=concurrency + 20,
                                                 keepalive_expiry=120))

    # 预热各一次（建立 TLS 连接，排除握手对首轮的影响）
    await sdk.chat.completions.create(model=MODEL, messages=MSG, max_tokens=6)
    await pool.post(QB.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {QWEN_KEY}"},
                    json={"model": MODEL, "messages": MSG, "max_tokens": 6})

    print(f"并发={concurrency} 轮次={rounds}  端点=qwen兼容模式 chat  max_tokens=6\n")
    sdk_wall, sdk_lat, http_wall, http_lat = [], [], [], []
    for i in range(rounds):
        w, l = await bench_sdk(concurrency, sdk)
        sdk_wall.append(w); sdk_lat += l
        w2, l2 = await bench_http(concurrency, pool)
        http_wall.append(w2); http_lat += l2
        print(f"  轮{i+1}: SDK 墙钟={w:.2f}s QPS={concurrency/w:.1f} 均={statistics.mean(l):.0f}ms"
              f" | http_pool 墙钟={w2:.2f}s QPS={concurrency/w2:.1f} 均={statistics.mean(l2):.0f}ms")

    print("\n===== 汇总（越低的墙钟/QPS 越高越优）=====")
    print(f"SDK      : 平均墙钟={statistics.mean(sdk_wall):.2f}s  平均QPS={concurrency/statistics.mean(sdk_wall):.1f}"
          f"  时延均={statistics.mean(sdk_lat):.0f}ms  P95={_pct(sdk_lat,95):.0f}ms")
    print(f"http_pool: 平均墙钟={statistics.mean(http_wall):.2f}s  平均QPS={concurrency/statistics.mean(http_wall):.1f}"
          f"  时延均={statistics.mean(http_lat):.0f}ms  P95={_pct(http_lat,95):.0f}ms")
    diff = (statistics.mean(sdk_wall) - statistics.mean(http_wall)) / statistics.mean(http_wall) * 100
    faster = "http_pool" if diff > 0 else "SDK"
    print(f"\n判定：{faster} 更快（差异 {abs(diff):.1f}%）；"
          f"两者共用同一 keep-alive 连接池，差异主要来自 SDK 封装/对象构造开销。")
    await pool.aclose()


if __name__ == "__main__":
    asyncio.run(main())
