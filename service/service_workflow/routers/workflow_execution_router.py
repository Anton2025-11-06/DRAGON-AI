# -*- coding: utf-8 -*-
"""工作流执行域路由:执行(异步)/ SSE 订阅 / 控制 / 调试 / API Key / 文件上传。

对应前端契约(需求 4:全部统一异步执行,已取消同步 execute):
- POST /workflow-executions/workflows/{id}/execute-async  → 返回 executionId
- GET  /workflow-executions/{executionId}/subscribe   (SSE,EventSource 不能带自定义头,
  此端点不做权限装饰器,鉴权由网关完成)
- 控制(需求 5):pause/resume/cancel 由 API 改执行记录表状态;
  resume 可选携带 edit 变量 body({"variables": {...}}),合并进快照后生效。
- 其余 variables/snapshot 与 workflow-api-keys/*、workflow-files/*。

路由顺序注意:/workflows/{workflow_id}/execute-async 与 /page 为固定段,
必须先于 /{execution_id} 动态段声明。
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Body, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_workflow.schemas.workflow_schema import (
    ApiKeyCreateReq, WorkflowExecutionReq,
)
from service.service_workflow.services.workflow_apikey_service import WorkflowApiKeyService
from service.service_workflow.services.workflow_execution_service import WorkflowExecutionService
from service.service_workflow.services.workflow_file_service import WorkflowFileService

router = APIRouter(prefix="/workflow-executions", tags=["工作流执行"])


async def _user_id(request: Request) -> int:
    try:
        login_user = await get_login_user(request)
        return int(login_user.get("user_id") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ==================== 固定路径(先声明) ====================

@router.post("/workflows/{workflow_id}/execute-async", summary="异步执行(返回 executionId)")
async def execute_workflow_async(request: Request, workflow_id: int, body: WorkflowExecutionReq):
    """投递执行任务到 arq 队列,立即返回 executionId(需求 4:统一异步模式)。

    trigger 判定:请求头带 X-Workflow-Token(网关 API Key 模式翻译) → API 触发,
    API 触发只执行已发布版本快照,且要求工作流已发布(见 _prepare_execution)。
    """
    trigger = "API" if request.headers.get("X-Workflow-Token") else "DEBUG"
    execution_id = await WorkflowExecutionService.execute_async(
        workflow_id, body, user_id=await _user_id(request), trigger_type=trigger)
    return ApiResponse.success(data=execution_id, message="任务已提交")


@router.get("/page", summary="分页查询执行历史")
@has_permission("workflow:execution:list")
async def page_executions(request: Request, current: int = 1, size: int = 10,
                          workflowId: str = None, status: str = None):
    data = await WorkflowExecutionService.page_executions(
        current=current, size=size, workflow_id=workflowId, status=status)
    return ApiResponse.success(data=data)


# ==================== 单次执行 ====================

@router.get("/{execution_id}", summary="获取执行详情")
@has_permission("workflow:execution:list")
async def get_execution(request: Request, execution_id: str):
    data = await WorkflowExecutionService.get_execution(execution_id)
    if data is None:
        return ApiResponse.error(400, "执行记录不存在")
    return ApiResponse.success(data=data)


@router.get("/{execution_id}/node-executions", summary="节点执行明细（调试面板数据源）")
@has_permission("workflow:execution:list")
async def get_node_executions(request: Request, execution_id: str):
    return ApiResponse.success(data=await WorkflowExecutionService.node_executions(execution_id))


@router.get("/{execution_id}/subscribe", summary="SSE 订阅执行事件流")
async def subscribe_execution(request: Request, execution_id: str):
    return StreamingResponse(
        WorkflowExecutionService.subscribe_events(execution_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx/网关代理缓冲，保证 token 实时推送
        })


@router.post("/{execution_id}/pause", summary="暂停执行（断点/手动）")
@has_permission("workflow:execution:run")
async def pause_execution(request: Request, execution_id: str):
    ok = await WorkflowExecutionService.pause(execution_id)
    if not ok:
        return ApiResponse.error(400, "执行不存在或已结束，无法暂停")
    return ApiResponse.success(message="已请求暂停")


@router.post("/{execution_id}/resume", summary="恢复执行(可携带 edit 变量)")
@has_permission("workflow:execution:run")
async def resume_execution(request: Request, execution_id: str,
                           body: Optional[dict] = Body(default=None)):
    """恢复暂停的执行(需求 5):改 DB 状态 RUNNING + 投递 resume 任务。

    body 可选 {"variables": {...}}——前端 edit 的全局变量,合并进快照后生效。
    """
    variables = (body or {}).get("variables") if body else None
    ok = await WorkflowExecutionService.resume(execution_id, variables=variables)
    if not ok:
        return ApiResponse.error(400, "执行不存在或未处于暂停状态")
    return ApiResponse.success(message="已恢复执行")


@router.post("/{execution_id}/cancel", summary="取消执行")
@has_permission("workflow:execution:run")
async def cancel_execution(request: Request, execution_id: str):
    ok = await WorkflowExecutionService.cancel(execution_id)
    if not ok:
        return ApiResponse.error(400, "执行不存在或已结束")
    return ApiResponse.success(message="已请求取消")


@router.put("/{execution_id}/variables/{variable_name}", summary="调试：更新全局变量")
@has_permission("workflow:execution:run")
async def update_variable(request: Request, execution_id: str, variable_name: str,
                          value: Any = Body(...)):
    ok = await WorkflowExecutionService.update_variable(execution_id, variable_name, value)
    if not ok:
        return ApiResponse.error(400, "执行不存在或已结束")
    return ApiResponse.success(message="变量已更新")


@router.get("/{execution_id}/snapshot", summary="调试：获取执行快照")
@has_permission("workflow:execution:list")
async def get_execution_snapshot(request: Request, execution_id: str):
    data = await WorkflowExecutionService.get_snapshot(execution_id)
    if data is None:
        return ApiResponse.error(400, "执行记录不存在")
    return ApiResponse.success(data=data)


@router.post("/{execution_id}/resume-from-snapshot", summary="调试：从快照恢复执行")
@has_permission("workflow:execution:run")
async def resume_from_snapshot(request: Request, execution_id: str,
                               snapshot: dict = Body(...)):
    try:
        resp = await WorkflowExecutionService.resume_from_snapshot(execution_id, snapshot)
        return ApiResponse.success(data=resp)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


# ==================== 工作流维度执行历史 ====================
# 注意：/workflows/{workflow_id}/executions 是动态段嵌套，放在 /{execution_id} 之后
# 不影响匹配（execution_id 路径不带 /workflows 前缀段），但语义上必须两段匹配，安全。

@router.get("/workflows/{workflow_id}/executions", summary="工作流执行历史")
@has_permission("workflow:execution:list")
async def get_workflow_executions(request: Request, workflow_id: int,
                                  current: int = 1, size: int = 10, status: str = None):
    data = await WorkflowExecutionService.page_executions(
        current=current, size=size, workflow_id=str(workflow_id), status=status)
    return ApiResponse.success(data=data)


# ==================== API Key ====================

api_key_router = APIRouter(prefix="/workflow-api-keys", tags=["工作流API Key"])


@api_key_router.post("", summary="创建 API Key（明文仅返回一次）")
@has_permission("workflow:apikey:add")
async def create_api_key(request: Request, body: ApiKeyCreateReq):
    try:
        resp = await WorkflowApiKeyService.create(
            body.workflowId, body.name, body.rateLimit or 0, body.expireDays,
            user_id=await _user_id(request))
        return ApiResponse.success(data=resp, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@api_key_router.get("/workflows/{workflow_id}", summary="工作流的 API Key 列表")
@has_permission("workflow:apikey:list")
async def list_api_keys(request: Request, workflow_id: int):
    return ApiResponse.success(data=await WorkflowApiKeyService.list_by_workflow(workflow_id))


@api_key_router.put("/{api_key_id}/status", summary="更新 API Key 状态")
@has_permission("workflow:apikey:edit")
async def update_api_key_status(request: Request, api_key_id: int, status: str = Query(...)):
    try:
        ok = await WorkflowApiKeyService.update_status(api_key_id, status)
        if not ok:
            return ApiResponse.error(400, "API Key 不存在")
        return ApiResponse.success(message="状态已更新")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@api_key_router.delete("/{api_key_id}", summary="删除 API Key")
@has_permission("workflow:apikey:delete")
async def delete_api_key(request: Request, api_key_id: int):
    ok = await WorkflowApiKeyService.delete(api_key_id)
    if not ok:
        return ApiResponse.error(400, "API Key 不存在")
    return ApiResponse.success(message="删除成功")


# ==================== 文件 ====================

file_router = APIRouter(prefix="/workflow-files", tags=["工作流文件"])


@file_router.post("/upload", summary="上传单个文件")
async def upload_file(request: Request, file: UploadFile = File(...)):
    try:
        info = await WorkflowFileService.upload(file, user_id=await _user_id(request))
        return ApiResponse.success(data=info)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@file_router.post("/upload-batch", summary="批量上传文件")
async def upload_files(request: Request, files: List[UploadFile] = File(...)):
    try:
        infos = await WorkflowFileService.batch_upload(files, user_id=await _user_id(request))
        return ApiResponse.success(data=infos)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@file_router.get("/{file_id}", summary="获取文件信息")
async def file_info(request: Request, file_id: str):
    info = await WorkflowFileService.info(file_id)
    if info is None:
        return ApiResponse.error(400, "文件不存在")
    return ApiResponse.success(data=info)


@file_router.delete("/{file_id}", summary="删除文件")
async def delete_file(request: Request, file_id: str):
    ok = await WorkflowFileService.delete(file_id)
    if not ok:
        return ApiResponse.error(400, "文件不存在")
    return ApiResponse.success(message="删除成功")
