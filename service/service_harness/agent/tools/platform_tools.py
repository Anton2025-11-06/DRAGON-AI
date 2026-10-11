# -*- coding: utf-8 -*-
"""平台工具桥：tb_tool 动态 Python 工具 → agentscope 工具（受限沙箱执行）。

设计口径：
- 参数定义来自 ToolService.parse_parameters（parameters_schema 的归一结果），
  转成 JSON Schema 作为 input_schema——模型按定义传参，绑定行的
  sourceType/value 那套是画布概念，这里不适用；
- 执行走 ToolService.call（py_sandbox 受限执行 + 编译校验 + 超时控制），
  与工作流工具节点、工具页「运行测试」同一颗函数——三处跑出不同结果
  是这类动态工具最难自证清白的问题；
- 工具名 ``tool_{id}_{name}``：id 保唯一（tb_tool.name 不查重）、前缀防撞
  agentscope 内置工具；
- load_for_node 只认启用中的工具：挂载后被人停用的，本轮组装即跳过（与
  MCP ensure_usable 同一口径）。

本模块只被 arq worker import（同 harness_agent 的口径）。
"""
from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior, PermissionContext, PermissionDecision,
)
from agentscope.tool import ToolBase

# 参数定义 type → JSON Schema type 的折算表；认不出的回落 string
#（严格拒掉会让「定义里 type 拼错」的工具直接不可用，宽松回落只是少一层校验）
_TYPE_MAP = {
    "str": "string", "string": "string", "text": "string",
    "int": "integer", "integer": "integer", "long": "integer",
    "float": "number", "number": "number", "double": "number",
    "bool": "boolean", "boolean": "boolean",
    "list": "array", "array": "array", "object": "object", "dict": "object",
}

# 工具结果序列化长度上限：动态工具的返回不可控（可能整篇报告），超长截断
RESULT_MAX_CHARS = 2000


def params_to_schema(parameters: list[dict]) -> dict[str, Any]:
    """平台工具参数定义 → JSON Schema（input_schema）。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for define in parameters or []:
        name = str(define.get("name") or "").strip()
        if not name:
            continue
        raw_type = str(define.get("type") or "string").strip().lower()
        prop: dict[str, Any] = {"type": _TYPE_MAP.get(raw_type, "string")}
        if define.get("description"):
            prop["description"] = str(define["description"])
        if define.get("default") is not None:
            prop["default"] = define["default"]
        properties[name] = prop
        if define.get("required"):
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _format_result(value: Any) -> str:
    """执行结果 → 模型可读文本（JSON 序列化 + 超长截断）。"""
    try:
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > RESULT_MAX_CHARS:
        text = f"{text[:RESULT_MAX_CHARS]}…（结果过长已截断）"
    return text


def make_platform_tool(row: dict, parameters: list[dict]) -> ToolBase:
    """一条 tb_tool 行 → 一个 ToolBase 实例（闭包捕获源码与超时）。"""
    tool_id = int(row.get("id") or 0)
    tool_name = str(row.get("name") or f"tool{tool_id}")
    description = str(row.get("description") or "").strip()
    function_code = row.get("function_code") or ""
    timeout_ms = int(row.get("timeout") or 0)
    schema = params_to_schema(parameters)
    required = set(schema.get("required") or [])

    class PlatformBridge(ToolBase):
        """执行平台工具广场里的一个动态 Python 工具（受限沙箱）。"""

        name: str = f"tool_{tool_id}_{''.join(ch for ch in tool_name if ch.isalnum() or ch == '_')}"

        description: str = description or (
            f"平台工具「{tool_name}」（动态 Python 工具）。")

        input_schema: dict[str, Any] = schema

        is_read_only: bool = False
        is_concurrency_safe: bool = True
        is_state_injected: bool = False
        is_external_tool: bool = False

        async def check_permissions(
            self,
            tool_input: dict[str, Any],
            context: PermissionContext,
        ) -> PermissionDecision:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"平台工具 {tool_name} 按挂载清单调用，受限沙箱执行。",
            )

        async def call(self, **kwargs: Any):
            missing = sorted(required - set(kwargs or {}))
            if missing:
                return ToolChunk(
                    content=[TextBlock(
                        text=f"工具「{tool_name}」缺少必填参数: {missing}")],
                    state=ToolResultState.ERROR,
                )
            try:
                from service.service_workflow.services.tool_service import (
                    ToolService,
                )
                result = await ToolService.call(
                    function_code, kwargs or {}, timeout_ms=timeout_ms or None)
            except Exception as e:  # noqa: BLE001  执行失败回给模型，不炸整轮
                return ToolChunk(
                    content=[TextBlock(
                        text=f"工具「{tool_name}」执行失败："
                             f"{type(e).__name__}: {e}")],
                    state=ToolResultState.ERROR,
                )
            return ToolChunk(
                content=[TextBlock(text=_format_result(result))],
            )

    return PlatformBridge()


async def build_platform_tools(tool_ids: list[int]) -> tuple[list[Any], list[str]]:
    """挂载的平台工具 → 工具实例列表（停用/不存在的跳过并记日志）。

    :return: (工具实例列表, 生效的工具名列表——挂载摘要用)
    """
    from common.common_log.log_init import log

    from service.service_workflow.services.tool_service import ToolService

    tools: list[Any] = []
    names: list[str] = []
    for tool_id in [int(i) for i in tool_ids or []]:
        try:
            row = await ToolService.load_for_node(tool_id=tool_id)
        except Exception as e:  # noqa: BLE001  单工具失败不影响其它挂载
            log.warning("harness tool mount skipped: id={} {}", tool_id, e)
            continue
        parameters = ToolService.parse_parameters(row.get("parameters_schema"))
        tools.append(make_platform_tool(row, parameters))
        names.append(str(row.get("name") or f"tool{tool_id}"))
    return tools, names
