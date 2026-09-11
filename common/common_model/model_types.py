# -*- coding: utf-8 -*-
"""模型调用数据结构的公共定义（网关 / 工作流引擎共用）。

- 原定义于 service_workflow/workflow_engine/model_client.py，为支撑"直连 httpx /
  非直连 litellm"双路由下沉到 common 层，model_client.py 仅做 re-export 保持兼容。
- ModelConfig.model_url() 为直连模式（is_direct=1）的 URL 拼接逻辑；非直连走
  litellm_adapter，不再使用该方法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from common.common_constants.model_constant import MODEL_CATEGORY_TEXT_GEN


@dataclass
class ModelConfig:
    model_id: int
    name: str = ""
    category: str = MODEL_CATEGORY_TEXT_GEN
    provider: str = ""
    model_name: str = ""
    base_url: str = ""
    api_key: str = ""
    is_direct: int = 1
    suffixes: list = field(default_factory=list)
    # 节点配置所选接口后缀（非直连时优先用它拼接；None 则自动匹配 suffixes）
    use_suffix: Optional[str] = None
    model_params: dict = field(default_factory=dict)
    status: int = 1

    def model_url(self) -> str:
        """对话接口地址（LLM/分类/参数提取节点，仅直连模式使用）。

        直连：base_url 已含完整路径；非直连：base_url + 节点配置后缀 use_suffix
        （模型广场约定非直连必须选择后缀，未配置时返回 None 由业务层拦截）。
        （非直连模型现已由 litellm_adapter 路由，不再走本方法。）
        """
        if self.is_direct == 1:
            return self.base_url
        if self.use_suffix:
            return self.base_url.rstrip("/") + self.use_suffix
        return None


@dataclass
class ChatMessage:
    role: str  # system / user / assistant / tool
    content: Any  # str 或多模态数组
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list] = None

    def to_openai(self) -> dict:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            msg["name"] = self.name
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


@dataclass
class StreamChunk:
    content: str = ""
    reasoning_content: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None
    raw: Optional[dict] = None


@dataclass
class InvokeResult:
    content: str = ""
    reasoning_content: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class ModelNotFoundError(Exception):
    pass


class ModelInvokeError(Exception):
    """模型调用失败（HTTP 网络 / 上游错误 / litellm 协议映射）。

    status_code：可映射的上游状态码（401/429/404/...），供网关层直接转响应；
    litellm 适配层据此归一化异常，直连层保持原有透传语义。
    """

    def __init__(self, message: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code