import json
import os
import re
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from common.common_log.log_init import log
from common.common_constants.constant import (PREFIX_WORKFLOW_API_KEY, SERVICE_ALIASES)
from common.common_entity.response_schema import ApiResponse
from common.common_httpx.httpx import httpx_pool
from common.common_redis.redis import client
from common.common_utils.rate_limiter import RateLimiter
from service.service_gateway.util.model_proxy_router import model_proxy

router = APIRouter(tags=["业务网关"])

# 不透传给下游服务的请求头
_EXCLUDED_HEADERS = {"host", "content-length", "connection", "accept-encoding"}




@router.post("/api/model", summary="直连模型：api-key 鉴权，转发模型真实地址（完整路径）")
async def model_proxy_forward(request: Request):
    """直连：模型 base_url 维护的是完整接口路径，直接转发"""
    return await model_proxy(request, True)


@router.post("/api/model/{path:path}", summary="非直连模型：api-key 鉴权，base_url + 接口后缀转发")
async def model_proxy_entry(path: str, request: Request):
    """
    非直连：模型 base_url 维护的是接口基础地址，path 为接口后缀（如 v1/chat/completions），
    网关拼接 base_url + /path 后转发，并校验 path 在模型维护的后缀列表中
    """
    return await model_proxy(request, False, path)


@router.api_route("/api/{service_name}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                  summary="按服务名动态转发")
async def proxy(service_name: str, path: str, request: Request):
    """
    网关转发入口：/api/{service_name}/{下游路径}
    通过 Nacos 服务发现动态获取下游健康实例地址，透传方法/查询参数/请求头/请求体
    """

    # 模块名别名归一化：/api/system/... 映射到 Nacos 注册名 service_system
    def _normalize_service_name(name: str) -> str:
        """模块名别名 → Nacos 注册名（已带 service_ 前缀的路径原样转发）"""
        return SERVICE_ALIASES.get(name, None)

    service_name = _normalize_service_name(service_name)
    if service_name is None:
        raise HTTPException(status_code=404, detail="无效请求")

    # Nacos 发现失败（服务端不可用/实例未注册）时回退到静态实例表
    nacos_service = getattr(request.app.state, "nacos_service", None)
    target = None
    if nacos_service is not None:
        try:
            target = await nacos_service.get_one_healthy_instance(service_name)
        except Exception as e:
            log.warning(f"Nacos discovery failed for {service_name}: {e}")
    if target is None:
        raise HTTPException(status_code=503, detail=f"服务 {service_name} 无可用实例")

    ip, port = target
    url = f"http://{ip}:{port}/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_HEADERS}
    # 网关 TokenCheckMiddleware 校验通过后写入 scope["token"]
    # 跨服务，request.scope request.state读不到，改写到header下游消费用
    # 此处注入内部头 X-User-Token，下游可重复读，（覆盖客户端伪造值），下游服务直接读取即可，无需重复校验 token
    if request.scope.get("token"):
        headers["X-User-Token"] = request.scope["token"]
    body = await request.body()

    # ===== 工作流 API Key 模式：X-Workflow-Token 鉴权 + QPS 限流（需求 4.1，鉴权限流在网关处执行）=====
    if service_name == "service_workflow" and request.headers.get("X-Workflow-Token"):
        return await workflow_api_proxy(request, path, url, headers, body)

    return await forward_downstream(request, url, headers, body)


# ==================== 工作流 API Key 模式（第三方 API 执行） ====================

async def workflow_api_proxy(request: Request, path: str, url: str,
                             headers: dict, body: bytes):
    """
    API Key 模式代理：
    1. 只接受执行接口 /workflow-executions/workflows/{id}/execute-async（其余返回 403）
    2. 从 Redis 读取 X-Workflow-Token 对应配置（workflowId/rateLimit/expireTime/status）鉴权
    3. 按 Key 配置 QPS 限流（0=不限）
    4. 将请求体包装为符合执行接口的格式 {"inputs": ...}（宽松兼容直接传参对象）
    5. 透传 X-Workflow-Token 头，下游据此标记 trigger=API（只执行已发布版本）
    """
    match = re.match(r"^workflow-executions/workflows/(\d+)/execute-async$", path)
    if not match:
        return JSONResponse(status_code=403,
                            content=ApiResponse.error(403, "API Key 仅支持异步执行工作流接口"))
    workflow_id = int(match.group(1))

    token = (request.headers.get("X-Workflow-Token") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    raw = await client.hget(PREFIX_WORKFLOW_API_KEY, token)
    if not raw:
        return JSONResponse(status_code=401,
                            content=ApiResponse.error(401, "无效的 API Key"))
    try:
        cfg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return JSONResponse(status_code=401,
                            content=ApiResponse.error(401, "无效的 API Key"))
    if cfg.get("status") != "ACTIVE":
        return JSONResponse(status_code=403,
                            content=ApiResponse.error(403, "API Key 已停用"))
    if int(cfg.get("workflowId") or 0) != workflow_id:
        return JSONResponse(status_code=403,
                            content=ApiResponse.error(403, "API Key 无权调用该工作流"))
    expire_time = cfg.get("expireTime")
    if expire_time:
        try:
            if datetime.now() > datetime.strptime(expire_time, "%Y-%m-%d %H:%M:%S"):
                return JSONResponse(status_code=403,
                                    content=ApiResponse.error(403, "API Key 已过期"))
        except ValueError:
            return JSONResponse(status_code=401,
                                content=ApiResponse.error(401, "无效的 API Key"))

    rate_limit = int(cfg.get("rateLimit") or 0)
    if rate_limit > 0 and not await RateLimiter.check_workflow_api_qps(token, rate_limit):
        return JSONResponse(status_code=429,
                            content=ApiResponse.error(429, "请求频率超过 API Key 限制，请稍后再试"))

    # 包装 body 为执行接口格式；非 JSON 或已含 inputs/breakpoints 的原样透传
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and not ("inputs" in parsed or "breakpoints" in parsed):
                body = json.dumps({"inputs": parsed}, ensure_ascii=False).encode()
        except (json.JSONDecodeError, TypeError):
            pass
    else:
        body = b'{"inputs": {}}'
    return await forward_downstream(request, url, headers, body)


# ==================== 通用转发 ====================

async def forward_downstream(request: Request, url: str, headers: dict, body: bytes):
    """统一下游转发：SSE/流式端点流式转发（token 实时推送），其余整读整转。"""
    # SSE / 流式端点：必须流式转发。若用整读整转（client.request + resp.content），
    # 运行中/暂停中执行的事件流会被缓冲到流结束才下发，前端实时 token/暂停事件全丢。
    is_stream = url.endswith("/subscribe") or \
        "text/event-stream" in (request.headers.get("accept") or "")
    if is_stream:
        try:
            req = httpx_pool.client.build_request(
                method=request.method,
                url=url,
                params=request.query_params,
                headers=headers,
                content=body if body else None,
                timeout=httpx.Timeout(None, connect=10.0),  # 读无限（流生命周期由下游决定）
            )
            upstream = await httpx_pool.client.send(req, stream=True)
        except httpx.RequestError as e:
            log.error(f"Gateway stream-forward to {url} failed: {str(e)}")
            raise HTTPException(status_code=502, detail="下游服务请求失败")

        async def _stream_chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()  # 归还连接池

        resp_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
        return StreamingResponse(_stream_chunks(),
                                 status_code=upstream.status_code,
                                 headers=resp_headers,
                                 media_type=upstream.headers.get("content-type"))

    try:
        resp = await httpx_pool.client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            headers=headers,
            content=body if body else None,
        )
    except httpx.RequestError as e:
        log.error(f"Gateway forward to {url} failed: {str(e)}")
        raise HTTPException(status_code=502, detail="下游服务请求失败")

    resp_headers = {k: v for k, v in resp.headers.items()
                    if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers,
                    media_type=resp.headers.get("content-type"))
