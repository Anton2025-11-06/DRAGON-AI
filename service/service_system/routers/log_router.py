from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.schemas.rbac_schema import OperateLogQuery
from service.service_system.services.log_service import LogService

router = APIRouter(prefix="/logs", tags=["系统日志"])


@router.get("", summary="操作日志分页查询（审计）")
@has_permission("system:log:list")
async def list_operate_logs(
        request: Request,
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(10, ge=1, le=100, description="每页数量"),
        username: str = Query(None, description="操作人模糊查询"),
        module: str = Query(None, description="模块"),
        method: str = Query(None, description="请求方法"),
        status: int = Query(None, description="状态码"),
        trace_id: str = Query(None, description="链路追踪ID"),
        start_time: str = Query(None, description="开始时间 YYYY-MM-DD HH:mm:ss"),
        end_time: str = Query(None, description="结束时间 YYYY-MM-DD HH:mm:ss")):
    query = OperateLogQuery(username=username, module=module, method=method,
                            status=status, trace_id=trace_id,
                            start_time=start_time, end_time=end_time)
    return ApiResponse.success(data=await LogService.list_operate_logs(page, page_size, query))


@router.get("/trace/{trace_id}", summary="按 trace_id 查询链路明细")
@has_permission("system:log:list")
async def trace_detail(request: Request, trace_id: str):
    return ApiResponse.success(data=await LogService.trace_detail(trace_id))