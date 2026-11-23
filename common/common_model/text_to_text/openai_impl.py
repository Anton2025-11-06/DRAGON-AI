# -*- coding: utf-8 -*-
"""二级目录/供应商：openai —— 通用 OpenAI 兼容客户端（base_url 由配置决定）。"""
from common.common_constants.model_constant import PROVIDER_OPENAI
from common.common_model.base import register
from common.common_model.text_to_text import TextToTextBase


@register("text_to_text", PROVIDER_OPENAI)
class OpenAITextToText(TextToTextBase):
    """通用 OpenAI 协议文生文。base_url/api_key/model_name 全部来自 ModelConfig，
    不绑定任何具体厂商；深度思考按各家 OpenAI 兼容端共有的 body.enable_thinking 外发
    （见 _thinking_kwargs），未登记支持思考的老模型一律不传，传了反而 400。
    """

    provider = PROVIDER_OPENAI
    default_model = "gpt-4o-mini"

    def _thinking_kwargs(self, thinking: bool) -> dict:
        # 与 dashscope_impl 同一颗坑：Qwen3 混合推理模型（qwen-*-flash/plus 等）在
        # OpenAI 兼容端上 enable_thinking 默认就是 true。本仓登记的 openai provider 其实
        # 就是「base_url 指向通义兼容端」的通用客户端（见 model_registry 注），内部调用
        # （裁判/图谱抽取）从不传 thinking，若不显式关，非流式调用会把整段耗在思考上——
        # 评测里 context_precision 每行串行调 top_k 次，一次就撞满 AsyncOpenAI 的 120s
        # 超时，Executor 吞成一行不带指标名的 TimeoutError 日志、该项静默置 NaN。
        # 按 supports_thinking 闸门双向外发：登记支持思考的模型开/关都显式传值，未登记
        # 的老模型（不认 enable_thinking）返回 {} 不传。
        if not getattr(self.config, "supports_thinking", False):
            return {}
        return {"extra_body": {"enable_thinking": bool(thinking)}}
