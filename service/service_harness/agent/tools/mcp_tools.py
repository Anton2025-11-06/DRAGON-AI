# -*- coding: utf-8 -*-
"""MCP 桥：挂载的每个 MCP 连接现场 list_tools → 每个远端工具一个动态 ToolBase。

设计口径：
- 工具名 ``mcp_{server_slug}_{tool}``：前缀防撞 agentscope 内置工具（挂载的
  server 里可能就有个叫 bash 的工具），server 名做模型侧语义提示；
- 连接与调用全走 McpServerService（进程内 import，共库）：list_tools 取清单
  （组装时一次），call_tool 现场建连（每次调用）——不持有长连接，挂载中
  停用的连接在下一次调用会被 ensure_usable 拦住；
- 单 server 工具数超过 MAX_TOOLS_PER_SERVER 时截断（描述里带提示）：几十个
  工具全注册会把上下文撑爆，先保最常用的一批（MCP 返回顺序即 server 侧
  定义顺序），「选择装载」后续里程碑再做。

单个连接建连失败只跳过该连接（记 warning），不阻断其它挂载能力。

本模块只被 arq worker import（同 harness_agent 的口径）。
"""
from __future__ import annotations

import re
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior, PermissionContext, PermissionDecision,
)
from agentscope.tool import ToolBase

# 单个 MCP 连接最多注册的工具数（超过截断，见模块 docstring）
MAX_TOOLS_PER_SERVER = 30

# 工具名里的非法字符（agentscope 工具名要求函数名样式）
_NAME_SANITIZE = re.compile(r"[^0-9A-Za-z_]+")


def _slug(text: str, fallback: str) -> str:
    """server 名 → 工具名可用的短 slug（中文/符号折叠成下划线，保首尾干净）。"""
    slug = _NAME_SANITIZE.sub("_", str(text or "").strip()).strip("_")
    return slug[:24] or fallback


def _sanitize_schema(raw: Any) -> dict[str, Any]:
    """MCP inputSchema → agentscope input_schema（JSON Schema 同构，兜底空 object）。

    异形 schema（非 dict / 缺 properties）不硬塞：错误的 schema 会让 agentscope
    的参数校验在调用前就炸，空 object 至少让工具能被调用（模型按描述传参）。
    """
    if isinstance(raw, dict):
        schema = dict(raw)
        schema.setdefault("type", "object")
        if not isinstance(schema.get("properties"), dict):
            schema["properties"] = {}
        return schema
    return {"type": "object", "properties": {}}


def make_mcp_tool(mcp_id: int, server_name: str, tool: dict) -> ToolBase:
    """一个 MCP 远端工具 → 一个 ToolBase 实例（闭包捕获连接与工具标识）。"""
    tool_name = str(tool.get("name") or "").strip()
    description = str(tool.get("description") or "").strip()
    schema = _sanitize_schema(tool.get("inputSchema"))
    display = f"{server_name}/{tool_name}"

    class McpBridge(ToolBase):
        """调用挂载的 MCP 连接上的一个远端工具。"""

        name: str = f"mcp_{_slug(server_name, str(mcp_id))}_{_NAME_SANITIZE.sub('_', tool_name).strip('_')}"

        description: str = description or (
            f"MCP 连接「{server_name}」上的工具 {tool_name}。")

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
                message=f"MCP 工具 {display} 按挂载连接调用，平台侧已鉴权。",
            )

        async def call(self, **kwargs: Any):
            try:
                from service.service_workflow.services.mcp_service import (
                    McpServerService,
                )
                result = await McpServerService.call_tool(
                    mcp_id, tool_name, kwargs or {})
            except Exception as e:  # noqa: BLE001  调用失败回给模型，不炸整轮
                return ToolChunk(
                    content=[TextBlock(
                        text=f"MCP 工具 {display} 调用失败："
                             f"{type(e).__name__}: {e}")],
                    state=ToolResultState.ERROR,
                )
            parts = [str(result.get("content") or "").strip()]
            urls = result.get("urls") or []
            if urls:
                parts.append("资源链接：\n" + "\n".join(str(u) for u in urls))
            structured = result.get("structured")
            if structured:
                parts.append(f"结构化结果：{structured}")
            text = "\n".join(p for p in parts if p) or "(空结果)"
            if result.get("isError"):
                return ToolChunk(
                    content=[TextBlock(text=text)],
                    state=ToolResultState.ERROR,
                )
            return ToolChunk(content=[TextBlock(text=text)])

    return McpBridge()


async def build_mcp_tools(mcp_ids: list[int]) -> tuple[list[ToolBase], list[str]]:
    """挂载的 MCP 连接 → 工具实例列表（连接不通的跳过并记日志）。

    :return: (工具实例列表, 生效的连接名列表——挂载摘要用)
    """
    from common.common_log.log_init import log

    from service.service_workflow.services.mcp_service import McpServerService

    tools: list[ToolBase] = []
    server_names: list[str] = []
    for mcp_id in [int(i) for i in mcp_ids or []]:
        try:
            row = await McpServerService.get_by_id(mcp_id)
            McpServerService.ensure_usable(row)
            listed = await McpServerService.list_tools(row, mcp_id)
        except Exception as e:  # noqa: BLE001  单连接失败不影响其它挂载
            log.warning("harness mcp mount skipped: id={} {}", mcp_id, e)
            continue
        server_name = str(row.name or f"mcp{mcp_id}")
        truncated = listed[:MAX_TOOLS_PER_SERVER]
        for tool in truncated:
            tools.append(make_mcp_tool(mcp_id, server_name, tool))
        server_names.append(server_name)
        if len(listed) > MAX_TOOLS_PER_SERVER:
            log.warning("harness mcp tools truncated: server={} {}/{}",
                        server_name, MAX_TOOLS_PER_SERVER, len(listed))
    return tools, server_names
