# -*- coding: utf-8 -*-
"""Phase 2: 工作流大模型节点(LLMNodeExecutor)端到端真实联测。

真实链路(与线上执行引擎一致):
  START 节点输出变量 -> ExecutionContext.node_outputs
  LLMNodeExecutor.execute(ctx)
    -> WorkflowModelClient.create(model_id, provider=MockModelConfigProvider)  # 加载 ModelConfig
    -> cm_entry.supports(category, provider) 动态校验
    -> _build_kwargs(ctx, category) 解析 {{start.xxx}} 上游引用
    -> common_model 真实子类 ainvoke/astream(真实 key)
    -> _map_output 产出下游可引用变量

逐 (12 类型 x 可达供应商) 断言产出键非空。mock 仅替换「配置源」(ModelConfigProvider)，
模型调用与变量解析/产出映射全部走真实代码。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_httpx.httpx import httpx_pool
httpx_pool.init()

from common.common_model.model_types import ModelConfig
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.graph import Node, WorkflowGraph
from service.service_workflow.workflow_engine.nodes.ai_nodes import LLMNodeExecutor

ZHIPU_KEY = "14c3fcd7ebde480bad02730027164a54.WZP8K1okdwjNPgMm"
QWEN_KEY = "sk-f506cc7679a9446ba3fd937ddb7070ad"
ZB = "https://open.bigmodel.cn/api/paas/v4/"
QB = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROVIDER_URL = {"zhipu": (ZB, ZHIPU_KEY), "dashscope": (QB, QWEN_KEY), "openai": (QB, QWEN_KEY)}

IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
AUDIO = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"
VIDEO = "https://media.w3.org/2010/05/sintel/trailer.mp4"

# 每个 category: 节点配置(promptTemplate/媒体变量引用) + 期望产出键 + 校验函数
SCENARIO = {
    "text_to_text":     dict(cfg={"promptTemplate": "用一句话介绍大海", "streaming": True},
                             expect=lambda o: o.get("text")),
    "image_understand": dict(cfg={"promptTemplate": "图里有啥", "imageVariable": "start.imageUrl", "streaming": True},
                             expect=lambda o: o.get("text")),
    "video_understand": dict(cfg={"promptTemplate": "视频讲了啥", "videoVariable": "start.videoUrl", "streaming": True},
                             expect=lambda o: o.get("text")),
    "ocr":              dict(cfg={"imageVariable": "start.imageUrl"},
                             expect=lambda o: o.get("text") is not None),
    "text_embedding":   dict(cfg={"inputVariable": "start.text"},
                             expect=lambda o: o.get("dimension", 0) > 0),
    "image_embedding":  dict(cfg={"imageVariable": "start.imageUrl"},
                             expect=lambda o: o.get("dimension", 0) > 0),
    "text_rerank":      dict(cfg={"queryVariable": "start.query", "documentsVariable": "start.documents"},
                             expect=lambda o: bool(o.get("scores"))),
    "text_to_image":    dict(cfg={"promptTemplate": "一只戴帽子的猫"},
                             expect=lambda o: o.get("urls") or o.get("url")),
    "text_to_video":    dict(cfg={"promptTemplate": "一只猫在走路"},
                             expect=lambda o: bool(o.get("url"))),
    "image_to_video":   dict(cfg={"imageVariable": "start.imageUrl"},
                             expect=lambda o: bool(o.get("url"))),
    "text_to_audio":    dict(cfg={"promptTemplate": "你好世界"},
                             expect=lambda o: bool(o.get("url") or o.get("audio_url"))),
    "audio_to_text":    dict(cfg={"audioVariable": "start.audioUrl"},
                             expect=lambda o: bool(o.get("text"))),
}

# (cat, provider) -> model_name（仅列可达组合）
MODEL = {
    ("text_to_text", "dashscope"): "qwen-turbo", ("text_to_text", "zhipu"): "glm-4-flash", ("text_to_text", "openai"): "qwen-turbo",
    ("image_understand", "dashscope"): "qwen-vl-plus", ("image_understand", "zhipu"): "glm-4v-flash", ("image_understand", "openai"): "qwen-vl-plus",
    ("video_understand", "dashscope"): "qwen-vl-plus", ("video_understand", "zhipu"): "glm-4v-plus",
    ("ocr", "dashscope"): "qwen-vl-ocr", ("ocr", "zhipu"): "glm-4v-flash",
    ("text_embedding", "dashscope"): "text-embedding-v3", ("text_embedding", "zhipu"): "embedding-3", ("text_embedding", "openai"): "text-embedding-v3",
    ("image_embedding", "dashscope"): "multimodal-embedding-v1",
    ("text_rerank", "dashscope"): "gte-rerank-v2", ("text_rerank", "zhipu"): "rerank",
    ("text_to_image", "dashscope"): "wanx2.1-t2i-turbo", ("text_to_image", "zhipu"): "cogview-3-flash",
    ("text_to_video", "dashscope"): "wanx2.1-t2v-turbo", ("text_to_video", "zhipu"): "cogvideox-flash",
    ("image_to_video", "dashscope"): "wanx2.1-i2v-turbo", ("image_to_video", "zhipu"): "cogvideox-flash",
    ("text_to_audio", "dashscope"): "qwen-tts", ("text_to_audio", "zhipu"): "glm-tts",
    ("audio_to_text", "dashscope"): "paraformer-v2", ("audio_to_text", "zhipu"): "glm-asr",
}

SLOW = {"text_to_image", "text_to_video", "image_to_video", "audio_to_text", "text_to_audio"}


class MockProvider:
    def __init__(self, cfg: ModelConfig):
        self._cfg = cfg

    async def get_model_config(self, model_id: int) -> ModelConfig:
        return self._cfg


class MockRuntime:
    def __init__(self, graph, provider):
        self.graph = graph
        self.model_provider = provider
        self.http_client = None
        self.usage = [0, 0]
        self.deltas = 0

    def bump_usage(self, p, c):
        self.usage[0] += p or 0
        self.usage[1] += c or 0

    async def emit(self, event, **kw):
        if event == "node.delta":
            self.deltas += 1


def build_ctx(category, provider, model_name):
    base, key = PROVIDER_URL[provider]
    cfg_model = ModelConfig(model_id=1, name=f"{category}-{provider}", category=category,
                            provider=provider, model_name=model_name, base_url=base,
                            api_key=key, status=1)
    scen = SCENARIO[category]["cfg"]
    node_data = {"modelId": 1, "outputVariable": "output", **scen}
    raw = {
        "nodes": [
            {"id": "start", "type": "START", "label": "开始", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "llm1", "type": "LLM", "label": "LLM", "position": {"x": 100, "y": 0}, "data": node_data},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "llm1"}],
    }
    graph = WorkflowGraph(raw)
    ctx = ExecutionContext(graph, inputs={})
    ctx.set_node_output("start", {
        "imageUrl": IMG, "audioUrl": AUDIO, "videoUrl": VIDEO,
        "text": "你好世界，今天天气不错", "query": "苹果",
        "documents": ["苹果手机很好用", "香蕉是黄色的", "橙子是水果"],
    })
    runtime = MockRuntime(graph, MockProvider(cfg_model))
    node = graph.get_node("llm1")
    return node, runtime, ctx


async def run_one(category, provider, model_name):
    node, runtime, ctx = build_ctx(category, provider, model_name)
    ex = LLMNodeExecutor(node, runtime)
    t0 = time.monotonic()
    try:
        result = await ex.execute(ctx)
    except Exception as e:  # noqa: BLE001
        ms = int((time.monotonic() - t0) * 1000)
        return {"cat": category, "provider": provider, "model": model_name, "status": "EXC",
                "ms": ms, "keys": [], "detail": f"{type(e).__name__}: {str(e)[:200]}", "out": None}
    ms = int((time.monotonic() - t0) * 1000)
    out = result.output or {}
    ok = SCENARIO[category]["expect"](out)
    if ok:
        status = "PASS"
    else:
        status = "EMPTY-OUTPUT"
    detail = {k: (str(v)[:60] if not isinstance(v, (int, float)) else v) for k, v in out.items()
              if k != "output"}
    return {"cat": category, "provider": provider, "model": model_name, "status": status,
            "ms": ms, "keys": list(out.keys()), "detail": detail, "deltas": runtime.deltas, "out": out}


async def main():
    combos = [(cat, prov, name) for (cat, prov), name in MODEL.items()]
    fast = [c for c in combos if c[0] not in SLOW]
    slow = [c for c in combos if c[0] in SLOW]

    async def batch(items, conc):
        sem = asyncio.Semaphore(conc)

        async def guard(t):
            async with sem:
                return await run_one(*t)
        return await asyncio.gather(*[guard(t) for t in items])

    results = await batch(fast, 5)
    results += await batch(slow, 2)

    from collections import Counter
    cnt = Counter(r["status"] for r in results)
    lines = ["===== Phase2 工作流 LLM 节点端到端真实联测 (LLMNodeExecutor.execute) =====",
             f"组合数={len(combos)}  汇总: {dict(cnt)}", ""]
    order = {"PASS": 0, "EMPTY-OUTPUT": 1, "EXC": 2}
    results.sort(key=lambda r: (order.get(r["status"], 9), r["cat"], r["provider"]))
    for r in results:
        tag = f"[{r['status']:12s}]"
        stream = f" deltas={r.get('deltas', 0)}" if r.get("deltas") else ""
        lines.append(f"{tag} {r['cat']:16s} x {r['provider']:9s} {r['model']:24s} {r['ms']:6d}ms "
                     f"keys={r['keys']}{stream}\n             -> {r['detail']}")
    txt = "\n".join(lines) + "\n"
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_p2_result.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    asyncio.run(main())
