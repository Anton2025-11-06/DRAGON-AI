import asyncio
import json
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from common.common_entity.rbac_entity import OperateLog
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

# 仅记录写操作（读操作量大且无状态变更，不落库）
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 参数最大记录长度（避免超长请求体撑爆日志表）
_MAX_PARAMS_LEN = 2000


# 敏感字段脱敏：日志落库前将密码类/凭证类字段替换为掩码，防止明文泄露
_SENSITIVE_KEYS = {"password", "old_password", "new_password", "confirm_password", "token", "authorization"}


async def _persist_log(record: "OperateLog") -> None:
    """后台异步落库：审计日志不得阻塞业务响应（同步 commit 在高并发写请求下
    会因连接池排队而拖慢接口）；数据对象已与请求/scope 解耦，失败仅告警"""
    try:
        async with mysql_client.get_session() as session:
            session.add(record)
            await session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning(f"OperateLogMiddleware persist failed: {str(e)}")


def _mask_sensitive(data: dict) -> dict:
    """递归脱敏：将敏感键的值替换为 ***"""
    if not isinstance(data, dict):
        return data
    return {
        k: ("***" if k.lower() in _SENSITIVE_KEYS else _mask_sensitive(v) if isinstance(v, dict) else v)
        for k, v in data.items()
    }


class OperateLogMiddleware(BaseHTTPMiddleware):
    """
    系统操作日志中间件：将每个写请求（增删改）落库 tb_operate_log，
    记录操作人、模块、参数、IP、耗时、状态码与 trace_id，供"系统日志"页面查询审计。
    注意：日志入库失败不影响业务响应（try/except 兜底）。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method

        # 仅记录写操作，其余直通
        if method not in _WRITE_METHODS:
            return await call_next(request)

        start_time = time.perf_counter()
        status_code = 500
        error_msg = None

        try:
            params = {"query": dict(request.query_params)}
            try:
                body = await request.body()
                if body:
                    params["body"] = json.loads(body.decode("utf-8"))
            except Exception:
                # 请求体可能为空/非 JSON（如表单、文件），忽略解析失败
                pass
        except Exception as e:
            log.error(f"OperateLogMiddleware parse request failed: {str(e)}")
            params = {}

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            # 业务异常：记录后继续上抛，由全局异常处理器统一返回
            error_msg = str(e)[:_MAX_PARAMS_LEN]
            raise
        finally:
            cost_ms = round((time.perf_counter() - start_time) * 1000, 2)
            # 操作人信息在 call_next 之后读取：TokenCheckMiddleware 将用户载荷写入
            # request.scope（跨中间件共享引用），state 仅绑定当前 Request 实例故先读 scope
            user = request.scope.get("login_user") or getattr(request.state, "login_user", None) or {}
            parts = path.split("/")
            service_name = parts[2] if len(parts) > 2 and parts[1] == "api" else (parts[1] if len(parts) > 1 else "")
            # 异步写库：响应立即返回，后台任务落库，失败仅告警不影响主流程
            try:
                log_record = OperateLog(
                    trace_id=request.scope.get("trace_id") or getattr(request.state, "trace_id", "-"),
                    user_id=user.get("user_id"),
                    username=user.get("username") if user.get("username") else request.headers.get("x-user-api-key"),
                    module=service_name,
                    operation=f"{path}",
                    method=method,
                    path=path,
                    params=json.dumps(_mask_sensitive(params), ensure_ascii=False)[
                        :_MAX_PARAMS_LEN] if params else None,
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent", "")[:255],
                    status=status_code,
                    cost_ms=cost_ms,
                    error_msg=error_msg,
                )
                asyncio.create_task(_persist_log(log_record))
            except Exception as e:
                log.warning(f"OperateLogMiddleware persist failed: {str(e)}")
