# -*- coding: utf-8 -*-
"""RAGAS 评测的 langchain-core 适配器：把 RAGAS 需要的 LLM / Embeddings 转发到本仓模型层。

为什么要这一层
--------------
RAGAS 只认 langchain 的 ``BaseChatModel`` / ``Embeddings`` 抽象，而本仓的生成与向量都走
``common_model``（openai 兼容直连），**没有任何 langchain provider**。这里只借 langchain-core
的两个抽象基类做一层薄适配，把调用转回 ``RagModelService.chat`` / ``embed_texts``，
既不引入额外厂商依赖，也让 ragas 只出现在 ragflow worker 侧。

传 model_id 而不是 ModelConfig
------------------------------
``BaseChatModel`` / ``Embeddings`` 都是 pydantic 模型，挂一个任意类型的 config 字段要额外
声明 ``arbitrary_types_allowed``。这里只传 ``model_id``（int），调用时再 ``require_config``
取配置，既避开 pydantic 摩擦，也让每次调用都拿到缓存里的最新配置（模型改了参数即时生效）。

同步入口的桥接
--------------
RAGAS 在异步里跑，优先命中 ``_agenerate`` / ``aembed_*``；langchain 的同步抽象
（``_generate`` / ``embed_query``）也必须实现。桥接函数在无事件循环时用 ``asyncio.run``，
已在循环里时拒绝（那种调用点应由异步路径接住），避免「运行中的 loop 再开 asyncio.run」。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, Sequence

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ChatGeneration, ChatResult  # noqa: F401
from pydantic import ConfigDict, Field

from common.common_model.model_types import ModelConfig
from service.service_rag.services.rag_model import RagModelService


def _run_sync(coro: Any) -> Any:
    """在同步上下文里跑一个协程：无 loop 时用 asyncio.run，已有 loop 时明确拒绝。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("评测适配器不应在已运行的事件循环里走同步路径（请用异步入口）")


def _split_messages(messages: Sequence[BaseMessage]) -> tuple[str, str]:
    """langchain 消息列表 → (system 文本, user prompt 文本)。

    RAGAS 一般把整段指令塞进一条 HumanMessage，但保险起见把所有 system 角色并进 system、
    其余按顺序拼成一段 prompt（AIMessage 也当普通文本带上，裁判偶尔要参考多轮）。
    """
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    for msg in messages or []:
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        role = getattr(msg, "type", None) or ""
        if role == "system":
            if text:
                system_parts.append(text)
        else:
            if text:
                prompt_parts.append(text)
    return "\n\n".join(system_parts), "\n\n".join(prompt_parts)


async def _resolve_config(model_id: int, label: str,
                          config: Optional[ModelConfig]) -> ModelConfig:
    """优先用调用方在主管线里解析好的 config（打分线程不碰 MySQL）；没带则按 id 现取。"""
    if config is not None:
        return config
    return await RagModelService.require_config(model_id, label)


class RagasChatModel(BaseChatModel):
    """RAGAS 的 LLM 适配：优先用传入的 config，否则按 model_id 取，转发 RagModelService.chat（text_to_text）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_id: int = 0
    label: str = "评测模型"
    config: Optional[ModelConfig] = Field(default=None)

    @property
    def _llm_type(self) -> str:  # langchain 要求的一个标识，仅用于日志与序列化
        return "dragon-ragas-chat"

    async def _agenerate(self, messages: Sequence[BaseMessage], stop: Optional[list[str]] = None,
                         run_manager: Any = None, **kwargs: Any) -> ChatResult:
        system, prompt = _split_messages(messages)
        config = await _resolve_config(self.model_id, self.label, self.config)
        text = await RagModelService.chat(config, prompt, system=(system or None))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _generate(self, messages: Sequence[BaseMessage], stop: Optional[list[str]] = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        # 同步路径给非异步调用兜底（RAGAS 走 _agenerate，一般命中不到这里）
        return _run_sync(self._agenerate(messages, stop, run_manager, **kwargs))


class RagasEmbeddings(Embeddings):
    """RAGAS 的向量适配：优先用传入的 config，否则按 model_id 取，转发 embed_texts / embed_query。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_id: int = 0
    label: str = "向量模型"
    config: Optional[ModelConfig] = Field(default=None)

    async def _aembed_documents(self, texts: list[str]) -> list[list[float]]:
        config = await _resolve_config(self.model_id, self.label, self.config)
        return await RagModelService.embed_texts(config, list(texts or []))

    async def _aembed_query(self, text: str) -> list[float]:
        config = await _resolve_config(self.model_id, self.label, self.config)
        return await RagModelService.embed_query(config, text)

    async def aembed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return await self._aembed_documents(texts)

    async def aembed_query(self, text: str, **kwargs: Any) -> list[float]:
        return await self._aembed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _run_sync(self._aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return _run_sync(self._aembed_query(text))


def build_judge_llm(model_id: int, *, label: str = "裁判模型",
                    config: Optional[ModelConfig] = None):
    """裁判 / 相似度打分用的 RAGAS LLM 包装（text_to_text 模型）。"""
    from ragas.llms import LangchainLLMWrapper

    return LangchainLLMWrapper(RagasChatModel(
        model_id=int(model_id or 0), label=label, config=config))


def build_embeddings(model_id: int, *, label: str = "向量模型",
                     config: Optional[ModelConfig] = None):
    """answer_relevancy 相似度用的 RAGAS Embeddings 包装（文本向量模型）。"""
    from ragas.embeddings import LangchainEmbeddingsWrapper

    return LangchainEmbeddingsWrapper(RagasEmbeddings(
        model_id=int(model_id or 0), label=label, config=config))
