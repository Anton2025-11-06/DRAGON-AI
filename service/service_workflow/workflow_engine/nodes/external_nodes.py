# -*- coding: utf-8 -*-
"""外部系统节点：HTTP_REQUEST / TOOL（动态函数工具）/ MCP_TOOL（MCP 工具）。

两类工具节点都不再经 runtime 钩子中转：直接调 service 层（函数内延迟 import，
避开 engine ← nodes ← services 的模块级循环），参数绑定行与「代码执行」节点同构。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Optional

from service.service_workflow.workflow_engine import py_sandbox
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
)


class HttpRequestNodeExecutor(BaseNodeExecutor):
    """HTTP_REQUEST：完整支持认证（Basic/Bearer/API_KEY）、请求体类型、重试退避。

    URL / headers / queryParams / body 值支持 {{变量引用}} 渲染。
    """

    node_type = "HTTP_REQUEST"

    def __init__(self, node, runtime):
        super().__init__(node, runtime)
        self._client = runtime.http_client

    async def _get_client(self):
        import httpx
        if self._client is None:
            cfg = self.config
            verify = bool(cfg.get("sslVerify", True))
            self._client = httpx.AsyncClient(
                verify=verify,
                timeout=httpx.Timeout(
                    float(cfg.get("readTimeout") or 60000) / 1000,
                    connect=float(cfg.get("connectTimeout") or 10000) / 1000),
            )
        return self._client

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        url = ctx.render(str(cfg.get("url") or ""))
        if not url:
            raise ValueError("HTTP 请求节点未配置 URL")
        method = (cfg.get("method") or "GET").upper()
        headers = {k: ctx.render(v) for k, v in (cfg.get("headers") or {}).items()}
        params = {k: ctx.render(v) for k, v in (cfg.get("queryParams") or {}).items()}
        body = self._build_body(cfg, ctx)
        headers = self._apply_auth(headers, cfg.get("auth") or {}, ctx)

        retry_cfg = cfg.get("retry") or {}
        max_retries = int(retry_cfg.get("maxRetries", 0)) if retry_cfg.get("enabled") else 0
        retry_interval = int(retry_cfg.get("retryInterval", 1000))
        backoff = float(retry_cfg.get("backoffMultiplier", 2))
        retry_codes = set(retry_cfg.get("retryStatusCodes") or [429, 500, 502, 503, 504])

        client = await self._get_client()
        started = time.monotonic()
        last_error: Optional[str] = None
        resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = await client.request(method, url, headers=headers, params=params,
                                            json=body if isinstance(body, (dict, list)) else None,
                                            content=body if isinstance(body, (str, bytes)) else None)
                if resp.status_code in retry_codes and attempt < max_retries:
                    await asyncio.sleep(retry_interval * (backoff ** attempt) / 1000)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval * (backoff ** attempt) / 1000)
                    continue
                raise ValueError(f"HTTP 请求失败: {e}") from e

        if resp is None:
            raise ValueError(f"HTTP 请求失败: {last_error}")
        duration = int((time.monotonic() - started) * 1000)

        output_var = cfg.get("outputVariable") or "response"
        # 响应只填进配置的输出变量：顶层不再重复写 statusCode/body（同值别名，客户端会多出重复行）
        payload: dict = {"statusCode": resp.status_code}
        output = {output_var: payload, "duration": duration}
        if cfg.get("parseJsonResponse", True):
            try:
                payload["body"] = resp.json()
            except (json.JSONDecodeError, ValueError):
                payload["body"] = resp.text[:10000]
        else:
            payload["body"] = resp.text[:10000]
        # failOnError（默认 true）：非 2xx 视为节点失败；false 时错误响应作为输出返回，
        # 供下游条件分支处理（对齐 Dify/MaxKB 的 continue-on-error 用法）
        fail_on_error = cfg.get("failOnError")
        if fail_on_error is None:
            fail_on_error = True
        if resp.status_code >= 400 and fail_on_error:
            raise ValueError(f"HTTP 请求返回错误状态: {resp.status_code} {resp.text[:200]}")
        return NodeResult(output=output)

    def _build_body(self, cfg: dict, ctx: ExecutionContext):
        body_type = (cfg.get("bodyType") or "NONE").upper()
        body = cfg.get("body")
        if body is None or body_type == "NONE":
            return None
        if body_type in ("JSON", "RAW"):
            if isinstance(body, str):
                rendered = ctx.render(body)
                if body_type == "JSON":
                    try:
                        return json.loads(rendered)
                    except json.JSONDecodeError:
                        return rendered
                return rendered
            return ctx.render(body)
        if body_type == "X_WWW_FORM_URLENCODED":
            return {k: ctx.render(v) for k, v in (body or {}).items()}
        if body_type == "FORM_DATA":
            return {k: ctx.render(v) for k, v in (body or {}).items()}  # multipart 由 httpx 处理
        if body_type == "BINARY":
            return body if isinstance(body, (str, bytes)) else str(body)
        return body

    def _apply_auth(self, headers: dict, auth: dict, ctx: ExecutionContext) -> dict:
        atype = (auth.get("type") or "NONE").upper()
        if atype == "NONE":
            return headers
        if atype == "BEARER":
            headers["Authorization"] = f"Bearer {ctx.render(str(auth.get('bearerToken') or ''))}"
        elif atype == "BASIC":
            cred = f"{auth.get('username', '')}:{auth.get('password', '')}"
            headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
        elif atype == "API_KEY":
            header_name = auth.get("apiKeyHeader") or "Authorization"
            value = ctx.render(str(auth.get("apiKey") or ""))
            prefix = auth.get("apiKeyPrefix")
            headers[header_name] = f"{prefix} {value}".strip() if prefix else value
        return headers

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        url = (node.data or {}).get("url") or ""
        if not url:
            issues.append(issue("HTTP_NO_URL", "ERROR", "HTTP 请求节点未配置 URL", node))
        elif url.startswith("http://"):
            issues.append(issue("HTTP_INSECURE", "WARNING", "HTTP 请求使用非加密协议", node))
        return issues


class ToolNodeExecutor(BaseNodeExecutor):
    """TOOL 工具节点：调用 tb_tool 登记的动态 Python 函数工具。

    配置：toolId（工具下拉，toolName 仅作展示与旧图兼容）/ inputs（参数绑定行，
    与 CODE 节点同构：引用上游变量或自定义值）/ timeout（缺省取工具登记的超时）。
    执行环境复用 workflow_engine.py_sandbox：工具代码与代码节点一份口径，
    入口 main → run → 最后定义的顶层函数。
    输出与 CODE 节点一致为 {result: 返回值}，下游用 {{nodeId.result}} 引用。
    """

    node_type = "TOOL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        # 延迟 import：nodes 包由 engine 导入，模块级 import services 会绕成循环
        from service.service_workflow.services.tool_service import ToolService

        cfg = self.config
        tool_id = cfg.get("toolId")
        tool_name = cfg.get("toolName")
        if not tool_id and not tool_name:
            raise ValueError(f"节点「{self.node.label}」未选择工具")
        tool = await ToolService.load_for_node(
            int(tool_id) if tool_id else None, None if tool_id else str(tool_name))
        timeout = cfg.get("timeout")
        result = await ToolService.run_for_node(
            tool, cfg.get("inputs") or [], ctx.resolve_ref,
            int(timeout) if timeout else None)
        output_var = cfg.get("outputVariable") or "result"
        return NodeResult(output={output_var: ToolService.json_safe(result)})

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("toolId") and not data.get("toolName"):
            issues.append(issue("TOOL_NO_NAME", "ERROR", "工具节点未选择工具", node))
        return issues


class McpToolNodeExecutor(BaseNodeExecutor):
    """MCP_TOOL MCP 工具节点：调用 MCP 连接下的某个工具（官方 SDK 会话，现建现用）。

    配置：mcpServerId + toolName（工具下拉来自该连接的 tools/list）、
    inputs 参数绑定行（同 CODE / TOOL 节点），输出：
    - 配置的输出变量（默认 result）：structuredContent 优先，否则为文本 content
    - urls：图片/音频等资源链接（不重复的辅助键）
    isError 视为节点失败上抛（对齐 Dify/MaxKB：工具报错不静默成正常输出）。
    """

    node_type = "MCP_TOOL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        from service.service_workflow.services.mcp_service import McpServerService

        cfg = self.config
        server_id = cfg.get("mcpServerId")
        tool_name = cfg.get("toolName")
        if not server_id or not tool_name:
            raise ValueError(f"节点「{self.node.label}」未选择 MCP 连接或工具")
        arguments = py_sandbox.resolve_kwargs(cfg.get("inputs") or [], ctx.resolve_ref)
        result = await McpServerService.call_tool(int(server_id), str(tool_name), arguments)
        if result.get("isError"):
            raise ValueError(f"MCP 工具 {tool_name} 调用失败: {result.get('content') or '未知错误'}")
        content = result.get("content")
        structured = result.get("structured")
        output_var = cfg.get("outputVariable") or "result"
        return NodeResult(output={
            output_var: structured if structured is not None else content,
            "urls": result.get("urls") or [],
        })

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("mcpServerId"):
            issues.append(issue("MCP_NO_SERVER", "ERROR", "MCP 节点未选择连接", node))
        if not data.get("toolName"):
            issues.append(issue("MCP_NO_TOOL", "ERROR", "MCP 节点未选择工具", node))
        return issues
