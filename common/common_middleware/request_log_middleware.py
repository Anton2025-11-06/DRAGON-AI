import asyncio
import time
import uuid
from typing import Callable

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from common.common_log.log_init import log as logger, trace_id_var


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    请求链路日志中间件：
    1. 生成全局唯一 trace_id（UUID 前 12 位）写入 contextvar 与响应头 X-Trace-Id，
       loguru 日志自动携带，实现请求全链路追踪；若请求头已携带 X-Trace-Id（前端
       回带同一链路 ID 串联用户行为），则沿用该值而非重新生成
    2. 记录访问日志：IP / 方法 / 路径 / 状态码 / 耗时
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 优先沿用客户端回带的链路 ID（最长 64 位，与操作日志表 trace_id 列一致）；
        # 未携带时生成新的全局唯一 trace_id 并注入日志上下文（scope 跨中间件共享）
        incoming = (request.headers.get("X-Trace-Id")
                    or request.headers.get("x-trace-id") or "").strip()
        if incoming:
            trace_id = incoming[:64]
        else:
            trace_id = uuid.uuid4().hex[:12]
        # 回写请求头：request.headers 只读不可赋值，需改写 scope["headers"]；
        # 网关/微服务转发时透传 request.headers（见 gateway_router.proxy），
        # 下游即可读到同一 trace_id 串联全链路（已存在的同名头会被覆盖，幂等）
        mutable_headers = MutableHeaders(raw=request.scope.get("headers") or [])
        mutable_headers["X-Trace-Id"] = trace_id
        request.scope["headers"] = mutable_headers.raw

        trace_id_var.set(trace_id)
        request.scope["trace_id"] = trace_id
        request.state.trace_id = trace_id

        start_time = time.perf_counter()
        path = request.url.path
        method = request.method
        client_ip = request.client.host if request.client else "unknown"

        response: Response = await call_next(request)
        cost_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            f"[REQ_EXEC_STATUS: {response.status_code}] ip={client_ip} method={method} path={path} "
            f"status={response.status_code} cost={cost_ms}ms"
        )
        response.headers["X-Trace-Id"] = trace_id
        return response
