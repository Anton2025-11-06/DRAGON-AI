from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.services.rate_limit_service import (
    RateLimitConfigRequest, RateLimitConfigService)
from common.common_constants.constant import MODULES

router = APIRouter(prefix="/rate-limit", tags=["限流配置"])


@router.get("/modules", summary="可配置的模块桶候选列表")
@has_permission("system:rate-limit:list")
async def list_modules(request: Request):
    return ApiResponse.success(data=MODULES)


@router.get("/configs", summary="限流策略列表（已配置）")
@has_permission("system:rate-limit:list")
async def list_configs(request: Request):
    return ApiResponse.success(data=await RateLimitConfigService.list_configs())


@router.post("/configs", summary="保存限流策略（新增/更新）")
@has_permission("system:rate-limit:edit")
async def save_config(request: Request, body: RateLimitConfigRequest):
    await RateLimitConfigService.save_config(body)
    return ApiResponse.success(message="策略已保存到 Redis，请点击发布生效")


@router.delete("/configs/{bucket}", summary="删除限流策略（恢复无限流）")
@has_permission("system:rate-limit:edit")
async def delete_config(request: Request, bucket: str):
    removed = await RateLimitConfigService.delete_config(bucket)
    if not removed:
        return ApiResponse.error(400, "该策略不存在或已被删除")
    return ApiResponse.success(message="策略已删除，请点击发布生效")


@router.post("/publish", summary="发布限流策略到网关（刷新网关内存）")
@has_permission("system:rate-limit:publish")
async def publish(request: Request):
    result = await RateLimitConfigService.publish(request)
    return ApiResponse.success(data=result, message="已发布到网关")