import time

from fastapi import APIRouter
from starlette.requests import Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_token
from ..schemas.user_schema import Login, RegisterRequest
from ..services.login_service import LoginService

router = APIRouter(prefix="", tags=["登录登出和注册"])


@router.post("/login", summary="用户登录（签发 JWT）")
async def login(request: Login):
    result = await LoginService.login(request)
    return ApiResponse.success(result)


@router.post("/register", summary="用户注册")
async def register(request: RegisterRequest):
    await LoginService.register(request)
    return ApiResponse.success("注册成功，请登录")


@router.post("/logout", summary="用户登出（销毁登录态）")
async def logout(request: Request):
    # 网关已做 token 校验，通过后注入 X-User-Token 内部头供下游直取（内部服务不重复检查）；
    # 未经网关直达时回退从 Authorization 头提取
    await LoginService.logout(await get_token(request))
    return ApiResponse.success("登出成功")


@router.post("/refresh", summary="刷新 JWT（滑动续期）")
async def refresh(request: Request):
    token = await get_token(request)
    result = await LoginService.refresh_token(token)
    return ApiResponse.success(result)
