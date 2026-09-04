import time

from fastapi import APIRouter, Query, Request

from common.common_constants.constant import TOKEN_EXPIRE
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.services.online_service import OnlineService

router = APIRouter(prefix="/online", tags=["在线用户"])


@router.get("", summary="在线用户列表")
@has_permission("system:online:list")
async def list_online(request: Request,
                      page: int = Query(1, ge=1, description="页码"),
                      page_size: int = Query(10, ge=1, le=100, description="每页数量"),
                      keyword: str = Query(None, description="账号/真实姓名/部门名称/角色关键词")):
    return ApiResponse.success(data=await OnlineService.list_online(page, page_size, keyword))


@router.delete("/{jti}", summary="踢出在线用户")
@has_permission("system:online:kick")
async def kick_online(request: Request, jti: str):
    ok = await OnlineService.kick(jti)
    if not ok:
        return ApiResponse.error(400, "该登录态不存在或已失效")
    return ApiResponse.success(message="已踢出该用户")


@router.get("/internal/now", summary="内部探活：校验当前时间（联调用）")
async def internal_now(request: Request):
    return ApiResponse.success(data={"now": int(time.time()), "expire": TOKEN_EXPIRE})