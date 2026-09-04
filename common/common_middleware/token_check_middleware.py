from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from common.common_entity.response_schema import ApiResponse
from common.common_redis.redis import client
from common.common_constants.constant import PREFIX_LOGIN
from common.common_log.log_init import log
from common.common_utils.jwt_util import decode_token, get_token_jti


class TokenCheckMiddleware(BaseHTTPMiddleware):
    """
    Token 鉴权中间件：
    1. 白名单路径直接放行（登录/注册/接口文档）
    2. 优先校验 JWT 格式 token：解析签名/有效期，并以 jti 关联 Redis 中的
       登录态（login_{jti}），支持退出登录后强制失效
    3. 兼容历史 b32hex 格式 token（直接以 token 原文关联 Redis login_{token}）
    4. 校验通过后将 token 与登录用户载荷写入 request.state，供业务/权限层复用
    """
    WHITE_LIST_SUFFIX = [
        # ---- 登录服务侧下游路径（网关转发后为 /login /register /refresh）----
        "/login",
        "/register",
        "/docs",
        "/redoc",
        "/openapi.json",
    ]
    # 前缀白名单：服务间内部接口（网关 /internal/...）与外部模型调用（/api/model，api-key 鉴权）不校验用户登录态
    WHITE_LIST_PREFIX = [
        "/internal/",
        "/api/model",
    ]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 匹配任意白名单后缀直接放行
        if any(path.endswith(suffix) for suffix in self.WHITE_LIST_SUFFIX):
            return await call_next(request)
        # 匹配任意白名单前缀（内部接口）直接放行
        if any(path.startswith(prefix) for prefix in self.WHITE_LIST_PREFIX):
            return await call_next(request)

        # 提取 Authorization: Bearer <token>
        auth_header = request.headers.get("Authorization", "") or request.headers.get("authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else None

        # 无 token 直接拒绝
        if not token:
            return JSONResponse(status_code=403,
                                content=ApiResponse.error(code=403, message="未登录，禁止操作！"))

        # ---- 1. JWT 优先：签名 + 有效期校验，再以 jti 关联 Redis 登录态 ----
        payload = decode_token(token)
        if payload:
            jti = payload.get("jti")
            redis_key = PREFIX_LOGIN + (jti or token)
            if await client.exists(redis_key):
                # scope 为整个请求链共享的引用：内层中间件/路由处理器可读；
                # state 仅绑定当前 Request 实例，保留双写以兼容旧代码
                request.scope["token"] = token
                request.scope["login_user"] = payload
                request.state.token = token
                request.state.login_user = payload
                return await call_next(request)
            return JSONResponse(status_code=403,
                                content=ApiResponse.error(code=403, message="登录已失效，请重新登录！"))

        # ---- 2. 兼容历史 b32hex 格式 token：载荷存于 Redis login_{token} ----
        legacy = await client.get(PREFIX_LOGIN + token, to_dict=True)
        if legacy:
            request.scope["token"] = token
            request.scope["login_user"] = legacy
            request.state.token = token
            request.state.login_user = legacy
            return await call_next(request)

        # ---- 3. JWT 解析失败且无历史登录态：无效凭证，所有分支必须显式返回响应 ----
        return JSONResponse(status_code=403,
                            content=ApiResponse.error(code=403, message="无效的登录凭证，请重新登录！"))

