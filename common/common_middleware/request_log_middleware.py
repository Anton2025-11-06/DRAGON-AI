import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from common.common_log.log_init import log as logger, trace_id_var


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    请求链路日志中间件：
    1. 为每次请求生成全局唯一 trace_id（UUID 前 12 位），写入 contextvar 与响应头 X-Trace-Id，
       loguru 日志自动携带，实现请求全链路追踪
    2. 记录访问日志：IP / 方法 / 路径 / 状态码 / 耗时
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 生成链路追踪 ID 并注入日志上下文（scope 跨中间件共享，state 仅当前 Request 实例）
        trace_id = uuid.uuid4().hex[:12]
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