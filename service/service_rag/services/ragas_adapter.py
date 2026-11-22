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

同步抽象方法为什么是 NotImplementedError
------------------------------------------
langchain-core 1.x 把 ``BaseChatModel._generate`` 与 ``Embeddings.embed_documents`` /
``embed_query`` 列为抽象方法：不补它们，两个适配类在实例化时当场就抛 TypeError
（「打分一上来就失败」的直接原因，实测 1.6.9）。但不真实现：本仓模型层全异步且
httpx 连接池是进程级单例、绑在 worker 的事件循环上，同步线程里另起一个 loop 去跑
协程，等于拿另一个 loop 复用主 loop 的连接池，直接崩在「attached to a different
loop」这类错误上——比明确拒绝难查几个量级。而 RAGAS 的异步链路（``BaseRagasLLM.
generate → agenerate_text → agenerate_prompt``、``embed_texts`` 的 ``is_async`` 默认
True 且全库无人传 False，0.4.3 逐文件核对）全程只碰异步版，同步入口只有误用才会
碰到，所以补成带原因的 NotImplementedError，而不是埋一个跨 loop 的雷。

上面那句「只有误用才会碰到」在 0.4.3 其实是错的：``answer_relevancy`` 就从 async
``_ascore`` 里**同步**调了向量（见下面 build_relevancy_metric 的注释）。适配办法不是
给这里补一个跨 loop 的同步实现，而是把那个指标改成异步——指标本身照用 ragas 的提示词
与算分口径，只换掉取向量这一步。

"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: F401
from pydantic import ConfigDict, Field

from common.common_log.log_init import log
from common.common_model.model_types import ModelConfig
from service.service_rag.services.rag_model import RagModelService




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

    def _generate(self, messages: Sequence[BaseMessage], stop: Optional[list[str]] = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        # 必须存在（见模块 docstring），但不真实现：见 RagasEmbeddings.embed_documents 的注释。
        raise NotImplementedError(
            "RagasChatModel 仅支持异步调用（RAGAS aevaluate 走 _agenerate）；"
            "同步路径需跨事件循环复用 httpx 连接池，本仓不支持")

    async def _agenerate(self, messages: Sequence[BaseMessage], stop: Optional[list[str]] = None,
                         run_manager: Any = None, **kwargs: Any) -> ChatResult:
        system, prompt = _split_messages(messages)
        config = await _resolve_config(self.model_id, self.label, self.config)
        text = await RagModelService.chat(config, prompt, system=(system or None))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])



@dataclass
class RagasEmbeddings(Embeddings):
    """RAGAS 的向量适配：优先用传入的 config，否则按 model_id 取，转发 embed_texts / embed_query。

    用 dataclass 而不是 pydantic 字段：langchain-core 的 ``Embeddings`` 是纯 ABC
    （不像 ``BaseChatModel`` 是 pydantic 模型），``Field`` / ``ConfigDict`` 在这里
    根本不生效——原先那套写法连构造都过不了（``RagasEmbeddings() takes no
    arguments``），只是这颗雷一直被 RagasChatModel 先炸的错挡住没轮到暴露。
    """

    model_id: int = 0
    label: str = "向量模型"
    config: Optional[ModelConfig] = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # langchain-core 1.x 把同步 embed_documents / embed_query 也列为抽象方法：
        # 此前只写异步版，本类实例化同样会 TypeError，只是先被 RagasChatModel 的错
        # 挡住没轮到暴露。同样不真实现：向量调用（RagModelService.embed_texts →
        # common_model ainvoke）用同一个 httpx 异步单例，绑在 worker 的事件循环上，
        # 同步线程另起 loop 跑协程会拿另一个 loop 复用主 loop 的连接池，崩在
        # 「attached to a different loop」这类错误上，比明确拒绝难查几个量级。
        # RAGAS 0.4 异步链路只碰 aembed_documents / aembed_query（embed_texts 的
        # is_async 默认 True，全库无人传 False）。
        raise NotImplementedError(
            "RagasEmbeddings 仅支持异步调用（aembed_documents / aembed_query）；"
            "同步路径需跨事件循环复用 httpx 连接池，本仓不支持")

    def embed_query(self, text: str) -> list[float]:
        # 同 embed_documents：占位满足抽象契约，同步路径明确拒绝（见上方注释）
        raise NotImplementedError(
            "RagasEmbeddings 仅支持异步调用（aembed_documents / aembed_query）；"
            "同步路径需跨事件循环复用 httpx 连接池，本仓不支持")

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


def build_relevancy_metric():
    """``answer_relevancy`` 的异步改写版：只把「拿向量」这一步换成 await。

    ragas 0.4.3 的 ``ResponseRelevancy.calculate_similarity`` 是**同步**方法，里面直接调
    ``self.embeddings.embed_query()`` / ``embed_documents()``（langchain 同步 API），
    ``LangchainEmbeddingsWrapper`` 原样转给本仓 ``RagasEmbeddings`` —— 而那两个同步入口
    是刻意 ``raise NotImplementedError`` 的（见模块 docstring）。它是五个指标里唯一一个
    从 async ``_ascore`` 里同步调向量的，ragas 的 Executor 又把任务异常吞成一行
    ``Exception raised in Job[N]`` 日志并把该项置成 ``np.nan``，所以表现是「只有相关性
    这一列永远是 —，其余四项照常出分」——既不报错也没痕迹，比整场失败难查得多。

    反向生成问题（``question_generation.generate_multiple``，n=strictness）、noncommittal
    判定、余弦均值口径全部照抄 ragas 原实现，改的只是向量取值；不改 ragas 的提示词，
    也不给适配层埋跨事件循环的雷。
    """
    import numpy as np
    from ragas.metrics import _ResponseRelevancy
    from ragas.metrics._answer_relevance import ResponseRelevanceInput

    class AsyncResponseRelevancy(_ResponseRelevancy):
        """同 ragas ``ResponseRelevancy``，仅相似度那段走 ``aembed_query`` / ``aembed_documents``。"""

        async def _ascore(self, row: dict, callbacks: Any) -> float:
            assert self.llm is not None, "answer_relevancy 需要裁判模型"
            assert self.embeddings is not None, "answer_relevancy 需要向量模型"
            answers = await self.question_generation.generate_multiple(
                data=ResponseRelevanceInput(response=row["response"]),
                llm=self.llm, callbacks=callbacks, n=self.strictness)
            gen_questions = [str(a.question or "") for a in answers]
            if not gen_questions or all(not q.strip() for q in gen_questions):
                # 与 ragas 同口径：裁判没吐出问题就是 nan（原实现打一条 Invalid JSON 警告）
                log.warning("answer_relevancy：裁判没有反向生成出问题，本条不给分")
                return float("nan")
            # ragas 用 np.all(非0即真)：只有每一条都敷衍作答才算 noncommittal
            noncommittal = all(bool(int(a.noncommittal or 0)) for a in answers)
            q_vec = np.asarray(
                await self.embeddings.aembed_query(row["user_input"])).reshape(1, -1)
            g_vec = np.asarray(await self.embeddings.aembed_documents(
                gen_questions)).reshape(len(gen_questions), -1)
            norm = np.linalg.norm(g_vec, axis=1) * np.linalg.norm(q_vec, axis=1)
            sim = np.dot(g_vec, q_vec.T).reshape(-1) / norm
            return float(sim.mean() * int(not noncommittal))

    return AsyncResponseRelevancy()
