# -*- coding: utf-8 -*-
"""common_model 抽象层地基：结果类型 + 基类 + 流式 Mixin + 动态注册表 + 共享客户端。

设计要点（对齐需求「禁止桥接、注重异步高性能」）：
- 每个 (模型类型, 供应商) 组合 = 一个直接调用该供应商原生端点的子类，无任何跨厂商
  适配中间层（不用 litellm 之类桥接）；调用方式仅 openai SDK(AsyncOpenAI) 或 httpx。
- 支持关系「不硬编码」：谁 @register 过、能被真实调用，动态发现就返回谁。
- 高性能：AsyncOpenAI 与 httpx 均按 (base_url, api_key) 复用连接池客户端，避免每次
  重建 TLS 连接；原生非 OpenAI 端点(rerank / 异步视频 / multimodal-embedding)复用
  全局 common_httpx.httpx_pool。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncIterator, Optional

import asyncio

from openai import AsyncOpenAI

from common.common_constants.model_constant import MODEL_TYPES_STREAMABLE
from common.common_httpx.httpx import httpx_pool
from common.common_model.model_types import (ModelConfig, ModelInvokeError,
                                             StreamChunk)


# ==================== 统一结果类型 ====================
@dataclass
class ModelResult:
    """非流式统一返回。不同能力类型按需填充对应字段。

    - content：文本类结果（文生文/图片理解/视频理解/OCR/音频转文字 文本）
    - reasoning_content：思维链（thinking）内容
    - vectors：向量类结果（文本向量/图片向量）List[List[float]]
    - scores：重排结果 [{'index','relevance_score'}]
    - url / urls：生成类产物地址（文生图/视频/音频 直链）
    - audio_bytes：音频二进制（TTS 直接返回字节流时）
    - task_id：异步任务 ID（提交-轮询范式，未轮询完成时回传）
    - usage / raw：token 统计 / 原始响应体
    """
    content: str = ""
    reasoning_content: str = ""
    vectors: Optional[list] = None
    scores: Optional[list] = None
    url: Optional[str] = None
    urls: Optional[list] = None
    audio_bytes: Optional[bytes] = None
    task_id: Optional[str] = None
    parsed: Any = None
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


# ==================== 共享客户端工厂（连接池复用） ====================
@lru_cache(maxsize=64)
def openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """按 (base_url, api_key) 缓存 AsyncOpenAI 实例，复用其内部 httpx 连接池。

    max_retries=0：重试策略交由上层业务/网关统一控制，避免隐性重复计费。
    """
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=120.0, max_retries=0,
                       http_client=httpx_pool.client)


def http_client():
    return httpx_pool.client


async def poll_task(fetch, is_done, is_fail, *, interval: float = 3.0, timeout: float = 600.0):
    """通用「提交-轮询」异步任务等待器（文生图/文生视频/图生视频/ASR 等）。

    fetch: async () -> 任意任务快照；is_done/is_fail: 对快照判定完成/失败。
    轮询至完成/失败/超时，返回最后一次快照。性能：单次复用 http 连接，间隔休眠不阻塞事件循环。
    """
    import time
    start = time.monotonic()
    while True:
        snap = await fetch()
        if is_done(snap):
            return snap
        if is_fail(snap):
            raise ModelInvokeError(f"异步任务失败: {snap}")
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"异步任务轮询超时({timeout}s)")
        await asyncio.sleep(interval)


# ==================== 动态注册表 ====================
class ModelRegistry:
    """(类型, 供应商) → 实现类 的注册中心；提供双向动态发现，无硬编码支持矩阵。"""

    # {category: {provider: cls}}
    _table: dict[str, dict[str, type]] = {}

    @classmethod
    def register(cls, category: str, provider: str):
        def deco(real_cls: type):
            cls._table.setdefault(category, {})[provider] = real_cls
            return real_cls

        return deco

    @classmethod
    def get(cls, category: str, provider: str) -> type:
        try:
            return cls._table[category][provider]
        except KeyError:
            raise LookupError(f"未注册的能力类型/供应商组合: {category}/{provider}")

    @classmethod
    def providers_for(cls, category: str) -> list[str]:
        """动态获取：某模型类型有哪些供应商支持。"""
        return sorted(cls._table.get(category, {}).keys())

    @classmethod
    def categories_for(cls, provider: str) -> list[str]:
        """动态获取：某供应商支持哪些模型类型。"""
        return sorted(c for c, m in cls._table.items() if provider in m)

    @classmethod
    def matrix(cls) -> dict[str, list[str]]:
        """完整支持矩阵：{category: [providers]}，全部来自实际注册。"""
        return {c: sorted(m.keys()) for c, m in cls._table.items()}

    @classmethod
    def instantiate(cls, category: str, config: ModelConfig):
        """按 config.provider 取实现类并实例化。"""
        real_cls = cls.get(category, config.provider)
        return real_cls(config)


register = ModelRegistry.register


# ==================== 抽象基类 ====================
class BaseModel(abc.ABC):
    """所有能力类型抽象父类的公共基座。

    - category / provider 由子类以类属性声明（注册与发现依据）。
    - 子类持有 ModelConfig（base_url / api_key / model_name / model_params）。
    - 抽象方法：ainvoke（必选）、aparse（必选，解析返回供下游消费）。
    """

    category: str = ""
    provider: str = ""
    # 子类可声明该能力的默认模型标识（config.model_name 为空时兜底）
    default_model: str = ""

    def __init__(self, config: ModelConfig):
        self.config = config
        # 是否支持流式：由类型集合统一裁定，供上层校验/路由
        self.supports_stream = config is not None and self.category in MODEL_TYPES_STREAMABLE

    @property
    def model(self) -> str:
        """实际调用的模型标识：优先用配置，回退到子类默认。"""
        return getattr(self.config, "model_name", "") or self.default_model

    def oai(self) -> AsyncOpenAI:
        """取（连接池复用的）AsyncOpenAI 客户端，指向本供应商 OpenAI 兼容 base_url。"""
        return openai_client(self.config.base_url, self.config.api_key)

    @property
    def extra(self) -> dict:
        """模型扩展参数（model_params），透传给底层 SDK/HTTP。"""
        return dict(getattr(self.config, "model_params", None) or {})

    @abc.abstractmethod
    async def ainvoke(self, **kwargs) -> ModelResult:
        """非流式调用，返回统一 ModelResult。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def aparse(self, result: ModelResult) -> Any:
        """把 ModelResult 解析为下游需要的数据结构（如向量列表/文本/URL）。

        并非任何时候都需要解析：下游若要裸数据可直接用 ainvoke 的 ModelResult。
        """
        raise NotImplementedError


class StreamableMixin:
    """仅支持 stream 的能力类型混入：声明 astream 抽象方法。"""

    @abc.abstractmethod
    async def astream(self, **kwargs) -> AsyncIterator[StreamChunk]:
        """流式调用，逐块产出 StreamChunk（含 reasoning_content 思维链增量）。"""
        raise NotImplementedError


class ChatMLMixin:
    """对话族（OpenAI /chat/completions 协议）通用实现：文生文/图片理解/视频理解/OCR。

    非桥接：仍直接走本供应商 base_url 的 chat 端点（经 openai SDK）；此处仅做代码复用。
    供应商子类只需覆写 default_model 与 _thinking_kwargs（各家深度思考开关命名不同）。
    """

    def _thinking_kwargs(self, thinking: bool) -> dict:
        """深度思考参数钩子：默认无；子类按各家协议覆写。"""
        return {}

    def _build_params(self, kwargs: dict) -> dict:
        """合并 model_params + 运行时 kwargs + thinking 参数，剔除 None。"""
        thinking = bool(kwargs.pop("thinking", False))
        params = {**self.extra, **kwargs, **self._thinking_kwargs(thinking)}
        return {k: v for k, v in params.items() if v is not None}

    async def _chat_invoke(self, messages: list, **kwargs) -> ModelResult:
        """非流式 chat：产出含 content/reasoning_content/usage 的 ModelResult。"""
        resp = await self.oai().chat.completions.create(
            model=self.model, messages=messages, **self._build_params(kwargs))
        msg = resp.choices[0].message
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return ModelResult(content=msg.content or "",
                           reasoning_content=getattr(msg, "reasoning_content", "") or "",
                           usage=usage, raw=resp.model_dump())

    async def _chat_stream(self, messages: list, **kwargs) -> AsyncIterator[StreamChunk]:
        """流式 chat：逐块产出增量 content 与 reasoning_content。"""
        params = self._build_params(kwargs)
        params["stream"] = True
        stream = await self.oai().chat.completions.create(
            model=self.model, messages=messages, **params)
        async for chunk in stream:
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            yield StreamChunk(content=(d.content or "") if d else "",
                              reasoning_content=(getattr(d, "reasoning_content", "") or "") if d else "",
                              finish_reason=chunk.choices[0].finish_reason)
