from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log


class UnauthorizedException(Exception):
    def __init__(self, message: str):
        # 调用父类构造，保存异常信息到 args
        super().__init__(message)
        self.message = message


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        log.error(f"HTTPException: {exc.status_code} - {exc.detail}")
        return JSONResponse(status_code=exc.status_code,
                            content=ApiResponse(code=exc.status_code, message="请求失败").dict())

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        error_details = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            error_details.append(f"{field}: {error['msg']}")
        error_message = "; ".join(error_details)
        log.error(f"ValidationError: {error_message}")
        return JSONResponse(status_code=422,
                            content=ApiResponse(code=422, message=f"参数校验失败").dict())

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        log.error(f"ValueError: {str(exc)}")
        # 透传业务异常文案（如“原密码错误”“部门下存在用户，无法删除”），
        # 避免所有 ValueError 都被硬编码成“参数错误”误导排查
        return JSONResponse(status_code=400,
                            content=ApiResponse(code=400, message=str(exc) or "参数错误").dict())

    @app.exception_handler(UnauthorizedException)
    async def unauth_exception_handler(request: Request, exc: UnauthorizedException):
        log.error(f"Unexpected error: {str(exc)}")
        return JSONResponse(status_code=403,
                            content=ApiResponse(code=403, message=exc.message).dict())

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        log.error(f"Unexpected error: {str(exc)}")
        return JSONResponse(status_code=500,
                            content=ApiResponse(code=500, message="服务器内部错误，请稍后再试").dict())
