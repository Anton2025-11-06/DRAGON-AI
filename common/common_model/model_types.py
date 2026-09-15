# -*- coding: utf-8 -*-
"""模型调用数据结构的公共定义（网关 / 工作流引擎共用）。

- 原定义于 service_workflow/workflow_engine/model_client.py，为支撑网关/工作流统一复用，
  下沉到 common 层，model_client.py 仅做 re-export 保持兼容。
- ModelConfig 描述一个已登记模型的能力形态（category=12 类型之一 / provider=3 家之一）；
  实际调用由 common_model 对应 (category, provider) 子类直连本厂商端点（无跨厂商桥接）。
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
    model_params: dict = field(default_factory=dict)
    status: int = 1
    # 模型管理登记的模型能力位（tb_model.supports_stream / supports_thinking）：
    # 与 BaseModel.supports_stream（该能力类型是否可流式）不同，这里表示该模型是否
    # 真开启流式/思考；上层引擎据此决定是否向厂商下发流式/思考入参，未登记不外发。
    supports_stream: bool = False
    supports_thinking: bool = False


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
    """模型调用失败（HTTP 网络 / 上游错误 / 端点协议映射）。

    status_code：可映射的上游状态码（401/429/404/...），供网关层直接转响应；
    各 common_model 子类据此归一化异常，网关按状态码透传。
    """

    def __init__(self, message: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code