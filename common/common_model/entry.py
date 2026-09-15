# -*- coding: utf-8 -*-
"""common_model 对外统一入口适配层（网关 / 模型测试按钮 / 工作流节点共用）。

职责：把「上层三种异构调用」翻译成 common_model 的类型化 ainvoke/astream，再把统一
ModelResult 翻译回上层需要的数据结构。这里**不是跨厂商桥接**——每个 (类型,供应商) 仍由
各自子类直连本厂商端点；本层只做「协议/字段形态」的转换，逻辑集中一处、三处复用。

三块能力：
1. config_from_row / build_config：模型行(ORM/Redis dict) → ModelConfig。
2. body_to_kwargs + result_to_openai + stream_to_openai_sse：网关 OpenAI 协议 ⇄ 类型化调用。
3. PROBE_PAYLOADS + test_model：模型测试按钮按类型构造真实探测入参并调用。
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

from common.common_constants.model_constant import (
    MODEL_TYPES_STREAMABLE, MT_AUDIO_TO_TEXT, MT_IMAGE_EMBEDDING,
    MT_IMAGE_TO_VIDEO, MT_IMAGE_UNDERSTAND, MT_MULTIMODAL_EMBEDDING, MT_OCR,
    MT_TEXT_EMBEDDING, MT_TEXT_RERANK, MT_TEXT_TO_AUDIO, MT_TEXT_TO_IMAGE,
    MT_TEXT_TO_TEXT, MT_TEXT_TO_VIDEO, MT_VIDEO_UNDERSTAND, PROVIDERS_ALL,
)
from common.common_model import instantiate, providers_for
from common.common_model.base import ModelResult
from common.common_model.model_types import (ModelConfig, ModelInvokeError,
                                             StreamChunk)

# ==================== ModelConfig 构造 ====================
def config_from_row(row: Any) -> ModelConfig:
    """ORM 实体 或 Redis 配置 dict → ModelConfig（两种来源字段名一致）。"""
    g = (lambda k: getattr(row, k, None)) if not isinstance(row, dict) else (lambda k: row.get(k))
    return ModelConfig(
        model_id=int(g("model_id") or g("id") or 0),
        name=g("name") or "",
        category=g("category") or "",
        provider=g("provider") or "",
        model_name=g("model_name") or "",
        base_url=g("base_url") or "",
        api_key=g("api_key") or "",
        model_params=g("model_params") or {},
        # 能力位透传（来源无此键时按未开启），供上层判定是否下流式/思考入参
        supports_stream=bool(g("supports_stream")),
        supports_thinking=bool(g("supports_thinking")),
        status=int(g("status") if g("status") is not None else 1),
    )


def supports(category: str, provider: str) -> bool:
    """动态判定：该 (类型, 供应商) 是否有 common_model 实现（不硬编码）。"""
    return provider in providers_for(category)


# ==================== 网关：OpenAI 请求体 → 类型化 ainvoke kwargs ====================
def _extract_text_and_images(messages: list) -> tuple[str, list[str]]:
    """从 OpenAI 多模态 messages 抽取纯文本 + 所有 image_url（供图片理解/OCR 用）。"""
    texts, images = [], []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, list):
            for part in c:
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    u = (part.get("image_url") or {}).get("url")
                    if u:
                        images.append(u)
    return "\n".join(t for t in texts if t), images


def body_to_kwargs(category: str, body: dict) -> dict:
    """把网关注册的 OpenAI 风格 body 翻译成对应类型 ainvoke/astream 的关键字入参。

    同时兼容「原生精简 body」（如 {prompt}/{input}/{query,documents}）与「OpenAI messages」。
    """
    messages = body.get("messages")
    if category == MT_TEXT_TO_TEXT:
        kw = {"messages": messages} if messages else {"prompt": body.get("prompt") or body.get("input")}
    elif category == MT_IMAGE_UNDERSTAND:
        if messages:
            txt, imgs = _extract_text_and_images(messages)
            kw = {"prompt": txt, "image_urls": imgs}
        else:
            kw = {"prompt": body.get("prompt", ""),
                  "image_urls": body.get("image_urls"), "image_url": body.get("image_url")}
    elif category == MT_VIDEO_UNDERSTAND:
        kw = {"prompt": body.get("prompt") or body.get("text", ""), "video_url": body.get("video_url")}
    elif category == MT_OCR:
        if messages:
            _, imgs = _extract_text_and_images(messages)
            kw = {"image_urls": imgs, "prompt": body.get("prompt")}
        else:
            kw = {"image_urls": body.get("image_urls"), "image_url": body.get("image_url"),
                  "prompt": body.get("prompt")}
    elif category == MT_TEXT_EMBEDDING:
        kw = {"input": body.get("input")}
    elif category == MT_IMAGE_EMBEDDING:
        kw = {"image_urls": body.get("image_urls"), "image_url": body.get("image_url")}
    elif category == MT_MULTIMODAL_EMBEDDING:
        kw = {"text": body.get("text") or body.get("input"),
              "texts": body.get("texts"),
              "image_urls": body.get("image_urls"), "image_url": body.get("image_url"),
              "video_urls": body.get("video_urls"), "video_url": body.get("video_url")}
    elif category == MT_TEXT_RERANK:
        kw = {"query": body.get("query"), "documents": body.get("documents") or [],
              "top_n": body.get("top_n")}
    elif category == MT_TEXT_TO_IMAGE:
        kw = {"prompt": body.get("prompt") or body.get("input"), "size": body.get("size"),
              "n": body.get("n")}
    elif category == MT_TEXT_TO_AUDIO:
        kw = {"text": body.get("input") or body.get("text"), "voice": body.get("voice")}
    elif category == MT_AUDIO_TO_TEXT:
        kw = {"audio_url": body.get("audio_url") or body.get("file_url")}
    elif category == MT_TEXT_TO_VIDEO:
        kw = {"prompt": body.get("prompt"), "size": body.get("size")}
    elif category == MT_IMAGE_TO_VIDEO:
        kw = {"image_url": body.get("image_url"), "prompt": body.get("prompt")}
    else:
        raise ModelInvokeError(f"网关不支持的能力类型: {category}")
    return {k: v for k, v in kw.items() if v is not None}


# ==================== 网关：ModelResult → OpenAI 风格响应体 ====================
def result_to_openai(category: str, model: str, r: ModelResult) -> dict:
    """把类型化 ModelResult 组装成对应 OpenAI 协议的响应 JSON（保持客户端 SDK 兼容）。"""
    if category in (MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR):
        msg = {"role": "assistant", "content": r.content}
        if r.reasoning_content:
            msg["reasoning_content"] = r.reasoning_content
        return {"object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                "usage": r.usage or {}}
    if category == MT_TEXT_EMBEDDING:
        data = [{"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(r.vectors or [])]
        return {"object": "list", "data": data, "model": model, "usage": r.usage or {}}
    if category == MT_IMAGE_EMBEDDING:
        data = [{"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(r.vectors or [])]
        return {"object": "list", "data": data, "model": model}
    if category == MT_MULTIMODAL_EMBEDDING:
        data = [{"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(r.vectors or [])]
        return {"object": "list", "data": data, "model": model, "usage": r.usage or {}}
    if category == MT_TEXT_RERANK:
        return {"model": model, "results": r.scores or []}
    if category == MT_TEXT_TO_IMAGE:
        urls = r.urls or ([r.url] if r.url else [])
        return {"created": int(time.time()), "data": [{"url": u} for u in urls]}
    if category in (MT_TEXT_TO_VIDEO, MT_IMAGE_TO_VIDEO):
        return {"object": "video", "model": model, "id": r.task_id,
                "status": "completed", "video_url": r.url, "raw": r.raw}
    if category == MT_TEXT_TO_AUDIO:
        # 直链优先；无 url 且有二进制时编码为 base64 data URI（同工作流 TTS 节点），前端可直接播放
        if not r.url and r.audio_bytes:
            import base64
            fmt = (r.raw or {}).get("format") or "wav"
            url = "data:audio/{};base64,{}".format(
                fmt, base64.b64encode(r.audio_bytes).decode())
        else:
            url = r.url
        return {"model": model, "url": url, "task_id": r.task_id}
    if category == MT_AUDIO_TO_TEXT:
        return {"task_id": r.task_id, "text": r.content}
    return {"model": model, "content": r.content, "raw": r.raw}


def _sse(obj: dict) -> str:
    """OpenAI SSE 帧。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def stream_to_openai_sse(category: str, model: str,
                               chunks: AsyncIterator[StreamChunk]) -> AsyncIterator[str]:
    """把 astream 的 StreamChunk 流封装成 OpenAI chat.completion.chunk SSE 文本流。"""
    first = True
    async for c in chunks:
        delta: dict[str, Any] = {}
        if first and c.content == "" and not c.reasoning_content:
            delta = {"role": "assistant"}
        else:
            if c.content:
                delta["content"] = c.content
            if c.reasoning_content:
                delta["reasoning_content"] = c.reasoning_content
        payload = {"object": "chat.completion.chunk", "model": model,
                   "choices": [{"index": 0, "delta": delta, "finish_reason": c.finish_reason}]}
        if c.usage:
            payload["usage"] = c.usage
        first = False
        yield _sse(payload)
        if c.finish_reason:
            break
    yield "data: [DONE]\n\n"


# ==================== 模型测试按钮：按类型构造真实探测入参 ====================
# 每类给一份最小可跑通的真实入参（远程可访问 URL，来自通义公共样例），使按钮能真实验证产出。
_PROBE_IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
_PROBE_AUDIO = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"

PROBE_PAYLOADS: dict[str, dict] = {
    MT_TEXT_TO_TEXT: {"prompt": "说一个字"},
    MT_TEXT_EMBEDDING: {"input": "你好"},
    MT_TEXT_RERANK: {"query": "苹果", "documents": ["苹果手机", "香蕉"]},
    MT_IMAGE_EMBEDDING: {"image_url": _PROBE_IMG},
    MT_MULTIMODAL_EMBEDDING: {"text": "一只戴帽子的猫", "image_url": _PROBE_IMG},
    MT_TEXT_TO_IMAGE: {"prompt": "一只戴帽子的猫"},
    MT_AUDIO_TO_TEXT: {"audio_url": _PROBE_AUDIO},
    MT_IMAGE_UNDERSTAND: {"prompt": "图里有啥", "image_url": _PROBE_IMG},
    MT_VIDEO_UNDERSTAND: {"prompt": "视频讲了啥", "video_url": "https://media.w3.org/2010/05/sintel/trailer.mp4"},
    MT_OCR: {"image_url": _PROBE_IMG},
    MT_TEXT_TO_VIDEO: {"prompt": "一只猫走路"},
    MT_IMAGE_TO_VIDEO: {"image_url": _PROBE_IMG},
    MT_TEXT_TO_AUDIO: {"text": "你好"},
}


def _summarize(category: str, r: ModelResult) -> str:
    """把产出压缩成一句人类可读摘要（按钮展示）。

    - 文本类：截断 80 字符（完整文本在 data.content 中）
    - 向量：展示维度 + 首条前 5 维真实值（完整值在 data.vectors.sample）
    - 重排：全部条目逐条列出（index + 得分），不做截断
    - 媒体/产物 URL：完整展示不截断（前端可直接点击/播放）
    """
    if r.vectors is not None:
        dim = len(r.vectors[0]) if r.vectors else 0
        head = [round(v, 4) for v in (r.vectors[0][:5] if r.vectors else [])]
        return f"向量维度={dim}，共 {len(r.vectors or [])} 条，首条前5维={head}"
    if r.scores is not None:
        items = "、".join(
            f"#{s.get('index')} 得分{s.get('relevance_score')}" for s in (r.scores or []))
        return f"重排 {len(r.scores or [])} 条：{items}"
    if r.audio_bytes is not None:
        return f"音频字节={len(r.audio_bytes)}"
    url = r.url or (r.urls[0] if r.urls else None)
    if url:
        return f"产物URL={url}"
    return f"文本={r.content[:80]!r}"


async def test_model(category: str, config: ModelConfig, *, with_stream: bool = True) -> dict:
    """模型测试按钮核心：按类型真实调用 common_model，返回 {success/message/latency_ms/data}。

    - 校验 provider 是否被该类型支持（动态发现，不硬编码）。
    - 支持 stream 的类型：非流式跑通后，再跑一次流式确认 astream 可用（with_stream=True）。
    """
    t0 = time.monotonic()
    if not supports(category, config.provider):
        return {"success": False,
                "message": f"{category} 类型下供应商 {config.provider} 无可用实现"
                           f"（当前支持：{providers_for(category) or '无'}）"}
    try:
        inst = instantiate(category, config)
        payload = dict(PROBE_PAYLOADS.get(category, {}))
        r: ModelResult = await inst.ainvoke(**payload)
        ms = int((time.monotonic() - t0) * 1000)
        summary = _summarize(category, r)
        stream_ok = None
        if with_stream and category in MODEL_TYPES_STREAMABLE:
            n = 0
            async for _ in inst.astream(**payload):
                n += 1
            stream_ok = n > 0
            summary += f"；流式 chunks={n}"
        msg = f"调用成功（{ms}ms）：{summary}"
        if stream_ok is False:
            msg = f"非流式成功但流式无输出（{ms}ms）"
        # 结构化真实产出：供结果弹窗直接渲染（文本全量 / 完整 URL 列表 / 向量样例 / 全部重排分）
        urls = list(r.urls or []) if r.urls else ([r.url] if r.url else [])
        if not urls and r.audio_bytes:
            # 智谱 glm-tts 直接返回字节流：编码为 base64 data URI（同工作流 TTS 节点），前端可直接播放
            import base64
            fmt = (r.raw or {}).get("format") or "wav"
            urls = ["data:audio/{};base64,{}".format(
                fmt, base64.b64encode(r.audio_bytes).decode())]
        vectors = None
        if r.vectors:
            dim = len(r.vectors[0]) if r.vectors else 0
            vectors = {"dim": dim, "count": len(r.vectors or []),
                       "sample": [round(v, 4) for v in (r.vectors[0][:8] if r.vectors else [])]}
        return {"success": True, "message": msg, "latency_ms": ms,
                "data": {"category": category, "provider": config.provider,
                         "model": inst.model, "summary": summary,
                         "content": r.content or None,
                         "urls": urls,
                         "vectors": vectors,
                         "scores": r.scores,
                         "audio_bytes_len": len(r.audio_bytes) if r.audio_bytes else None,
                         "usage": r.usage, "stream_chunks_ok": stream_ok}}
    except ModelInvokeError as e:
        return {"success": False, "latency_ms": int((time.monotonic() - t0) * 1000),
                "message": f"调用失败：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "latency_ms": int((time.monotonic() - t0) * 1000),
                "message": f"测试异常：{type(e).__name__}: {str(e)[:200]}"}
