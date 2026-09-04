from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_middleware.rate_limit_middleware import RateLimitMiddleware

router = APIRouter(prefix="/internal/rate-limit", tags=["网关内部-限流配置"])


@router.post("/reload", summary="从 Redis 重新加载限流策略到内存（系统管理发布时调用）")
async def reload_rate_limit(request: Request):
    policies = await RateLimitMiddleware.reload_from_redis()
    return ApiResponse.success(data={"policies": policies})


@router.get("/config", summary="查看当前内存限流策略（联调查看）")
async def get_rate_limit_config(request: Request):
    return ApiResponse.success(data={"policies": RateLimitMiddleware.snapshot()})