# -*- coding: utf-8 -*-
"""AI 模型网关：OpenAI 兼容统一入口 POST /api/model

调用链路：
1. api-key 鉴权：X-User-Api-Key: mk_xxx → Redis(model_rate_limit) 查映射
2. 模型一致性：body.model（模型标识）与 Redis(model_rate_limit_config) 中 model_name 比对
3. 限流：QPS（每秒固定窗口）+ 日调用上限（按天计数），Lua 原子计数
4. 管理端密钥回查 MySQL（明文密钥不进 Redis）
5. 按分类映射上游 OpenAI 兼容端点，body 包装（调用方参数原样透传，extra_body 展开合并）后转发
6. 流式响应 SSE 透传（httpx stream 边读边吐），非流式缓冲返回
"""
import json
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from service.service_gateway.util.model_gateway_cache import ModelGatewayCache
from common.common_utils.rate_limiter import RateLimiter

# 全局复用转发客户端：连接池 keep-alive；trust_env=False 跳过 Windows 系统代理
# 超时：连接 10s / 读 300s（LLM 长输出常见 30s+，流式首 token 等待放宽）
from common.common_httpx.httpx import httpx_pool


async def _forward(upstream: httpx.Response, stream: bool):
    """流式请求 → SSE 透传；失败响应（>=400）→ 读全文后按 OpenAI 错误映射返回"""
    if stream and upstream.status_code < 400:
        return StreamingResponse(upstream.aiter_bytes(),
                                 status_code=upstream.status_code,
                                 headers={"content-type": "text/event-stream",
                                          "cache-control": "no-cache"})
    # 非流式或上游错误：读取完整响应
    try:
        body = await upstream.aread()
    except Exception as e:  # noqa: BLE001
        log.error(f"read upstream response failed: {e}")
        Response(content="模型响应读取失败",
                 status_code=upstream.status_code,
                 media_type=upstream.headers.get("content-type", "application/json"))
    try:
        await upstream.aclose()
    except Exception:  # noqa: BLE001
        pass
    if upstream.status_code >= 400:
        # 错误透传：429 不暴露上游限流细节，统一 429 文案；其余按上游状态码
        detail = "模型服务错误"
        try:
            data = json.loads(body) if body else {}
            err = data.get("error") if isinstance(data, dict) else None
            detail = (err or {}).get("message", detail) if isinstance(err, dict) else detail
        except (json.JSONDecodeError, AttributeError):
            detail = body[:200].decode("utf-8", errors="ignore") or detail
        if upstream.status_code == 429:
            return Response(content="模型请求频率过快，请稍后再试",
                            status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type", "application/json"))
        return Response(content=detail,
                        status_code=upstream.status_code if upstream.status_code < 500 else 502,
                        media_type=upstream.headers.get("content-type", "application/json"))
    return Response(content=body,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/json"))


async def model_proxy(request: Request, forward: bool, suffix_url: str = None):
    """
    forward: 是否直连；
    是：模型管理base_url维护的模型base_url + suffix_url
    否：模型管理base_url维护的模型base_url，需要根据客户端request.url.path截取suffix_url进行 base_url + suffix_url转发
    suffix_url: base_url的后缀路径
    """
    # ==================== 1. api-key 鉴权 ====================
    api_key = (request.headers.get("X-User-Api-Key")
               or request.headers.get("x-user-api-key")
               or "")
    if not api_key:
        return ApiResponse.error(401, "API Key 为空")
    key_info = await ModelGatewayCache.find_key(api_key)
    if not key_info:
        return ApiResponse.error(401, "API Key 无效")
    if not key_info or not isinstance(key_info.get("model_id"), int):
        return ApiResponse.error(401, "指定的模型与授权的api-key不匹配")
    model_id = key_info["model_id"]

    # ==================== 2. 请求体解析 ====================
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return ApiResponse.error(400, "请求体必须是合法 JSON")
    if not isinstance(body, dict):
        return ApiResponse.error(400, "请求体必须是 JSON 对象")
    req_model = body.get("model")
    if not req_model or not isinstance(req_model, str):
        return ApiResponse.error(400, "缺少模型标识字段 model")

    # ==================== 3. 模型路由配置校验 ====================
    config = await ModelGatewayCache.get_config(model_id)
    if not config:
        return ApiResponse.error(404, "模型不存在，请联系管理员")
    if config.get("model_name") != req_model:
        return ApiResponse.error(404, "非授权模型，请确认API-KEY")
    if config.get("status") != 1:
        return ApiResponse.error(404, "模型已停用，请使用其他模型")

    # ==================== 4. 限流检查（QPS） ====================
    qps_limit = int(config.get("rate_limit_qps") or 0)
    if qps_limit > 0:
        if not await RateLimiter.check_model_qps(model_id, qps_limit):
            return ApiResponse.error(429, "模型请求频率超限（QPS），请稍后再试")

    # ==================== 5. 判断直连/非直连 → 拼上游端点 ====================
    base_url = (config.get("base_url") or "").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return ApiResponse.error(502, "模型接口地址未配置，请联系管理员")

    # 非直连：base_url（接口基础地址）+ 接口后缀；后缀需在模型维护的能力列表中（未配置列表则放行）
    if not forward:
        suffix = (suffix_url or "").strip()
        if not suffix:
            return ApiResponse.error(400, "非直连模型必须指定接口后缀（如 /v1/chat/completions）")
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        allowed = [s for s in (config.get("suffixes") or []) if s]
        if allowed and suffix not in allowed:
            return ApiResponse.error(404, f"该模型不支持接口后缀 {suffix}，请确认模型维护的接口能力")
        base_url = base_url + suffix

    headers = {
        "Authorization": f"Bearer {config.get('api_key')}",
        "Content-Type": "application/json",
    }
    stream = bool(body.get("stream"))

    # ==================== 7. 转发 ====================
    try:
        if stream:
            req = httpx_pool.client.build_request("POST", base_url, json=body, headers=headers)
            upstream = await httpx_pool.client.send(req, stream=True)
        else:
            upstream = await httpx_pool.client.post(base_url, json=body, headers=headers)
        log.info(f"Model proxy: model_id={model_id} model={req_model} "
                 f"stream={stream} upstream={upstream.status_code} ({base_url})")
        return await _forward(upstream, stream)
    except httpx.TimeoutException:
        log.error(f"model proxy timeout: {base_url}")
        return ApiResponse.error(504, "模型响应超时，请重试")
    except httpx.RequestError as e:
        log.error(f"model proxy forward failed: {base_url} {e}")
        return ApiResponse.error(502, "模型请求失败，请重试")
    except Exception as e:  # noqa: BLE001
        log.error(f"model proxy unexpected error: {e}")
        return ApiResponse.error(500, "内部错误，请联系管理员")
