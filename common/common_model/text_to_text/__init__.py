# -*- coding: utf-8 -*-
"""一级目录：文生文（text_to_text，支持 stream）。

基础父类 TextToTextBase：约定入参为 messages（OpenAI 规范）或 prompt，
非流式 ainvoke / 流式 astream / 解析 aparse。供应商子类放同目录 *_impl.py。
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from common.common_constants.model_constant import MT_TEXT_TO_TEXT
from common.common_model.base import (BaseModel, ChatMLMixin, ModelResult,
                                      StreamableMixin)
from common.common_model.model_types import StreamChunk


class TextToTextBase(BaseModel, ChatMLMixin, StreamableMixin):
    """文生文抽象父类。"""

    category = MT_TEXT_TO_TEXT

    def _to_messages(self, messages: Optional[list], prompt: Optional[str]) -> list:
        """入参归一：优先 messages，其次 prompt 包装为单轮 user。"""
        if messages:
            return messages
        if prompt is None:
            raise ValueError("文生文需提供 messages 或 prompt")
        return [{"role": "user", "content": prompt}]

    async def ainvoke(self, messages: Optional[list] = None, prompt: Optional[str] = None,
                      thinking: bool = False, **kwargs) -> ModelResult:
        """非流式对话。thinking=True 时按各家协议开启深度思考。"""
        return await self._chat_invoke(self._to_messages(messages, prompt),
                                       thinking=thinking, **kwargs)

    async def astream(self, messages: Optional[list] = None, prompt: Optional[str] = None,
                      thinking: bool = False, **kwargs) -> AsyncIterator[StreamChunk]:
        """流式对话，逐块产出增量内容 / 思维链。"""
        async for chunk in self._chat_stream(self._to_messages(messages, prompt),
                                             thinking=thinking, **kwargs):
            yield chunk

    async def aparse(self, result: ModelResult) -> Any:
        """解析：下游通常需要纯文本正文。"""
        return result.content


# 触发供应商子类注册（导入即 @register）
from . import openai_impl, dashscope_impl, zhipu_impl  # noqa: E402,F401
