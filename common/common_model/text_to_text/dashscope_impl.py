# -*- coding: utf-8 -*-
"""二级目录/供应商：通义千问 dashscope —— OpenAI 兼容模式 /compatible-mode/v1。"""
from common.common_constants.model_constant import (MT_TEXT_TO_TEXT,
                                                   PROVIDER_DASHSCOPE)
from common.common_model.base import register
from common.common_model.text_to_text import TextToTextBase


@register(MT_TEXT_TO_TEXT, PROVIDER_DASHSCOPE)
class DashscopeTextToText(TextToTextBase):
    """通义文生文。深度思考通过 body.enable_thinking=true 开启（Qwen 推理模型）。"""

    provider = PROVIDER_DASHSCOPE
    default_model = "qwen-turbo"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        # 通义 Qwen3 混合推理模型（qwen-*-flash/plus-latest 等）在 OpenAI 兼容端上
        # enable_thinking 默认就是 true：关闭态若不外发参数（旧写法会直接回 {}），非
        # 流式调用就会把整段耗在思考上——图谱抽取这种内部调用从不传 thinking，
        # 于是每批都撞满 120s 超时（j_failed 堆叠、全部文档图谱构建失败）。
        # 按 supports_thinking 闸门双向外发：登记支持思考的模型开/关都显式传值，未登
        # 记的老 qwen（不认 enable_thinking）一律不传，传了反而 400。
        if not getattr(self.config, "supports_thinking", False):
            return {}
        return {"extra_body": {"enable_thinking": bool(thinking)}}
