# -*- coding: utf-8 -*-
"""外部系统节点：HTTP_REQUEST / TOOL。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Optional

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
        output = {
            output_var: {"statusCode": resp.status_code},
            "statusCode": resp.status_code,
            "duration": duration,
        }
        if cfg.get("parseJsonResponse", True):
            try:
                output["body"] = resp.json()
                output[output_var]["body"] = output["body"]
            except (json.JSONDecodeError, ValueError):
                output["body"] = resp.text
                output[output_var]["body"] = resp.text[:10000]
        else:
            output["body"] = resp.text[:10000]
            output[output_var]["body"] = output["body"]
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
    """TOOL 工具节点：调用 tb_tool 动态函数工具 / tb_mcp_server MCP 工具。

    通过 runtime.tool_invoker 钩子注入（生产=ToolService/MCP 客户端；测试=内存实现）。
    toolParams 值支持 {{变量引用}}。
    """

    node_type = "TOOL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        tool_name = cfg.get("toolName")
        if not tool_name:
            raise ValueError("工具节点未配置工具名称")
        invoker = getattr(self.runtime, "tool_invoker", None)
        if not callable(invoker):
            raise ValueError("工具服务未接入（tool_invoker 未注册）")
        params = {k: ctx.render(v) if isinstance(v, str) else v
                  for k, v in (cfg.get("toolParams") or {}).items()}
        result = await invoker(
            tool_name=str(tool_name),
            mcp_server_id=cfg.get("mcpServerId"),
            params=params,
        )
        output_var = cfg.get("outputVariable") or "output"
        return NodeResult(output={output_var: result, "toolName": tool_name})

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        if not (node.data or {}).get("toolName"):
            issues.append(issue("TOOL_NO_NAME", "ERROR", "工具节点未选择工具", node))
        return issues
