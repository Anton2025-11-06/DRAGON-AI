import json
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from common.common_constants.constant import PREFIX_RATE_LIMIT_CONFIG
from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from common.common_redis.redis import client
from common.common_utils.rate_limiter import RateLimiter

_SERVICE_PATH = re.compile(r"^/api/([^/]+)")


def _bucket_of(request: Request) -> str:
    """把请求归类到限流桶：/api/{service}/... 取第一段，其余归 global"""
    path = request.url.path
    match = _SERVICE_PATH.match(path)
    if match:
        return match.group(1)
    return "global"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    网关级限流（配置驱动）：
    - 内存策略表由 Redis hash（rate_limit_config）驱动，网关启动与发布时加载
    - 未配置限流策略的模块默认“无限流”，直接放行
    - 已配置模块按 IP + 模块固定窗口计数，超限返回 429
    """

    # 内存限流策略：bucket -> {"limit": max 次数, "window": 窗口秒, "enabled": 是否启用}
    _policies: dict = {}

    @classmethod
    def _load_policies(cls, config: dict) -> dict:
        policies = {}
        for bucket, raw in (config or {}).items():
            if not raw:
                continue
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(raw, dict) or not bucket:
                continue
            policies[bucket] = {
                "limit": int(raw.get("limit") or 0),
                "window": int(raw.get("window") or 60),
                "enabled": bool(raw.get("enabled", True)),
            }
        cls._policies = policies
        return policies

    @classmethod
    async def reload_from_redis(cls) -> dict:
        """
        从 Redis 加载限流策略到内存（网关启动与“发布”时调用）。
        Redis 无任何配置时输出日志并保持空策略 = 默认无限流。
        """
        config = await client.hgetall(PREFIX_RATE_LIMIT_CONFIG)
        policies = cls._load_policies(config)
        if not policies:
            log.info("Rate limit config not found in redis, default: no rate limit (unlimited flow)")
        else:
            log.info(f"Rate limit config loaded from redis: {json.dumps(policies, ensure_ascii=False)}")
        return policies

    @classmethod
    def snapshot(cls) -> dict:
        """当前内存策略快照（供内部接口/联调查看）"""
        return {k: dict(v) for k, v in cls._policies.items()}

    async def dispatch(self, request: Request, call_next):
        if request.url.path.endswith(("/docs", "/redoc", "/openapi.json")):
            return await call_next(request)

        bucket = _bucket_of(request)
        policy = self._policies.get(bucket)
        # 未配置策略或策略停用 == 无限流
        if not policy or not policy.get("enabled"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if not await RateLimiter.check(client_ip, bucket, policy["limit"], policy["window"]):
            return JSONResponse(status_code=429,
                                content=ApiResponse.error(code=429, message="请求过于频繁，请稍后再试"))
        return await call_next(request)