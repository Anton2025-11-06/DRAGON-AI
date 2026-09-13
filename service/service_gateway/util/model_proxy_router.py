# -*- coding: utf-8 -*-
"""AI 模型网关：OpenAI 兼容统一入口 POST /api/model

调用链路：
1. api-key 鉴权：X-User-Api-Key: mk_xxx → Redis(model_rate_limit) 查映射
2. 模型一致性：body.model（模型标识）与 Redis(model_rate_limit_config) 中 model_name 比对
3. 限流：QPS（每秒固定窗口）+ 日调用上限（按天计数），Lua 原子计数
4. 管理端密钥回查 MySQL（明文密钥不进 Redis）
5. 按 Redis 配置的 category(12 类型) + provider(3 家) 走 common_model 分发到对应子类
   （各供应商子类直连本厂商端点，无任何跨厂商桥接），body 经 entry.body_to_kwargs 翻译为类型化入参
6. 产出经 entry 封装回 OpenAI 协议：支持 stream 的类型走 astream→SSE，其余 ainvoke→JSONResponse
"""
import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from service.service_gateway.util.model_gateway_cache import ModelGatewayCache
from common.common_utils.rate_limiter import RateLimiter
from common.common_model import entry as cm_entry
from common.common_model.model_types import ModelInvokeError


async def model_proxy(request: Request):
    """OpenAI 兼容统一入口：鉴权 + 限流后，按 Redis 配置的 (category, provider)
    交给 common_model 对应子类直连本厂商端点（端点由子类自拼，无接口后缀概念）。"""
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

    # ==================== 5. 校验上游基址 ====================
    base_url = (config.get("base_url") or "").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return ApiResponse.error(502, "模型接口地址未配置，请联系管理员")

    # ==================== 6. 走 common_model：按 (类型, 供应商) 分发到对应子类 ====================
    # 不再透传原始 HTTP：redis 配置的 category(12 类型) + provider(3 家) 决定具体实现，
    # 由各供应商子类直连本厂商端点（无任何跨厂商桥接）。
    category = config.get("category")
    provider = config.get("provider")
    if not cm_entry.supports(category, provider):
        return ApiResponse.error(404, f"模型能力类型/供应商暂不支持调用：{category}/{provider}")
    model_config = cm_entry.config_from_row(config)
    try:
        kwargs = cm_entry.body_to_kwargs(category, body)
    except ModelInvokeError as e:
        return ApiResponse.error(400, str(e))

    stream = bool(body.get("stream"))
    try:
        inst = cm_entry.instantiate(category, model_config)
        if stream and category in cm_entry.MODEL_TYPES_STREAMABLE:
            gen = cm_entry.stream_to_openai_sse(category, model_config.model_name,
                                                inst.astream(**kwargs))
            log.info(f"Model proxy(common_model stream): model_id={model_id} "
                     f"category={category} provider={provider} model={req_model}")
            return StreamingResponse(gen, media_type="text/event-stream",
                                     headers={"cache-control": "no-cache"})
        result = await inst.ainvoke(**kwargs)
        data = cm_entry.result_to_openai(category, model_config.model_name, result)
        log.info(f"Model proxy(common_model): model_id={model_id} category={category} "
                 f"provider={provider} model={req_model}")
        return JSONResponse(content=data)
    except ModelInvokeError as e:
        code = e.status_code or 502
        log.warning(f"model proxy common_model error: {category}/{provider} {e}")
        return Response(content=str(e.message),
                        status_code=code if 400 <= code < 500 else 502,
                        media_type="application/json")
    except Exception as e:  # noqa: BLE001
        log.error(f"model proxy unexpected error: {e}")
        return ApiResponse.error(500, "内部错误，请联系管理员")
