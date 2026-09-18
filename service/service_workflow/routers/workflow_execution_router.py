# -*- coding: utf-8 -*-
"""工作流执行域路由:执行(异步/同步) / 再提交 / SSE 订阅 / 控制 / API Key / 文件上传。

对应前端契约(需求 4:全部统一异步执行;需求 3:再提交接口):
- POST /workflow-executions/workflows/{id}/execute-async  → 返回 executionId
- WS   /workflow-executions/workflows/{id}/execute-sync    → 预览运行(事件走同一连接)
- POST /workflow-executions/{executionId}/submit      再提交(异步):RETRY 重新执行 /
  CONTINUE 审批后恢复,入参 {mode, inputs, approval}
- WS   /workflow-executions/{executionId}/submit-sync 再提交(同步,预览页审批入口)
- GET  /workflow-executions/{executionId}/subscribe   (SSE,EventSource 不能带自定义头,
  此端点不做权限装饰器,鉴权由网关完成)
- 控制(需求 5):cancel 由 API 改执行记录表状态,引擎每节点前查库生效。

已废弃(需求 2 决策、勿再引入):pause / resume / PUT variables / resume-from-snapshot /
断点。暂停只能由审批节点触发,恢复与重跑统一走 submit。设计冻结见
docs/workflow-approval-memory.md。

workflow-files 只做 HTTP 形参适配，存储能力全在 common.common_storage（ upload/download/
delete/exists），不落库：文件名（{uuid}_{原名}）就是唯一标识，上传返回的 URL 可匿名访问。

路由顺序注意:/workflows/{workflow_id}/execute-async 与 /page 为固定段,
必须先于 /{execution_id} 动态段声明。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketState

from common.common_entity.rbac_entity import OperateLog
from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from common.common_middleware.operate_log_middleware import _mask_sensitive, _persist_log
from common.common_permission.permission import (
    get_login_user, get_user_id, has_permission, is_admin,
)
from common.common_middleware.exception_handler import UnauthorizedException
from service.service_workflow.schemas.workflow_schema import (
    ApiKeyCreateReq, ApiKeyUpdateReq, WorkflowExecutionReq, WorkflowSubmitReq,
)
from service.service_workflow.services.workflow_apikey_service import WorkflowApiKeyService
from service.service_workflow.services.workflow_execution_service import WorkflowExecutionService

router = APIRouter(prefix="/workflow-executions", tags=["工作流执行"])

# 执行控制类端点的权限标识(同时支持 API Key 模式,故不走 @has_permission)
PERM_EXECUTION_RUN = "workflow:execution:run"


async def _auth_execution_control(request: Request, execution_id: str) -> int:
    """submit / cancel 的鉴权二选一,返回 user_id。

    - 带 X-Workflow-Token(第三方审批方,无登录态):校 key 有效且其绑定工作流
      正是这条执行所属的工作流;user_id 记 0(审批留痕落到 API-KEY)。
    - 页面调用:要登录态 + workflow:execution:run。

    不能直接用 @has_permission:它在 API Key 模式下拿不到 X-User-Token 会直接 401,
    而「外部系统拿 executionId 提交审批结论」正是本需求的主链路。
    """
    token = (request.headers.get("X-Workflow-Token") or "").strip()
    if token:
        await WorkflowExecutionService.check_api_key_scope(execution_id, token)
        return 0
    login_user = await get_login_user(request)
    if not is_admin(login_user) and PERM_EXECUTION_RUN not in (login_user.get("permissions") or []):
        raise UnauthorizedException("权限不足，禁止操作！")
    return int(login_user.get("user_id") or 0)


# ==================== WebSocket 端点公共辅助 ====================

async def _safe_send_json(websocket: WebSocket, payload: dict) -> None:
    """尽力而为地发一帧：未 accept / 客户端已断开 / 事件循环已收尾时静默跳过。

    异常分支里直接 send_json 会在「连接已断」时二次抛错，把真正的错误盖掉。
    """
    try:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


async def _safe_close(websocket: WebSocket) -> None:
    """显式完成 WS 关闭握手（发 close 帧）。

    必须显式 close：处理器直接 return 时 uvicorn 只掐 TCP 传输，不发 close 帧，
    网关侧 websockets 客户端会抛 ConnectionClosedError(no close frame received or
    sent)。注意 WebSocketState 是 IntEnum，不能用字符串 "connected" 比较。
    """
    try:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
    except Exception:  # noqa: BLE001
        pass


def _audit_ws_run(websocket: WebSocket, path: str, params: dict, login_user: dict,
                  start: float, status: int, error_msg: Optional[str]) -> None:
    """WS 端点手动审计：OperateLogMiddleware 基于 BaseHTTPMiddleware，对 websocket
    连接不触发，故在处理器 finally 里补落 tb_operate_log（失败仅告警，不阻断关闭）。

    :param path: /workflow-executions 前缀之后的相对路径(两个 WS 端点共用一套拼法)
    """
    try:
        full = f"api/workflow/workflow-executions{path}"
        record = OperateLog(
            trace_id=websocket.scope.get("trace_id") or uuid4().hex[:12],
            user_id=login_user.get("user_id"),
            username=login_user.get("username"),
            module="workflow",
            operation=full,
            method="WEBSOCKET",
            path=full,
            params=json.dumps(_mask_sensitive(params), ensure_ascii=False)[:2000],
            ip=websocket.client.host if websocket.client else None,
            user_agent=websocket.headers.get("user-agent", "")[:255],
            status=status,
            cost_ms=round((time.perf_counter() - start) * 1000, 2),
            error_msg=error_msg,
        )
        asyncio.create_task(_persist_log(record))
    except Exception as e:  # noqa: BLE001
        log.warning(f"workflow ws audit log failed: {str(e)}")


# ==================== 固定路径(先声明) ====================

@router.post("/workflows/{workflow_id}/execute-async", summary="异步执行(返回 executionId)")
async def execute_workflow_async(request: Request, workflow_id: int, body: WorkflowExecutionReq):
    """投递执行任务到 arq 队列,立即返回 executionId(需求 4:统一异步模式)。

    trigger 判定:请求头带 X-Workflow-Token(网关 API Key 模式翻译) → API 触发,
    API 触发只执行已发布版本快照,且要求工作流已发布(见 _prepare_execution)。
    """
    execution_id = await WorkflowExecutionService.execute_async(workflow_id, body, user_id=await get_user_id(request))
    return ApiResponse.success(data=execution_id, message="任务已提交")


@router.websocket("/workflows/{workflow_id}/execute-sync")
async def execute_workflow_sync(websocket: WebSocket, workflow_id: int):
    """同步执行(WebSocket)：接受连接后读取首帧入参（含网关注入的 login_user），
    执行过程中事件经同一连接实时回推，结束关闭连接。

    浏览器 WS 无法带 Authorization 头，登录态由网关解析后随首帧 login_user 下发；
    TokenCheck/OperateLog 等 BaseHTTPMiddleware 对 websocket scope 不生效，故此处
    把 login_user 写回 scope（供 get_user_id 复用）并手动落审计日志。
    """
    start = time.perf_counter()
    login_user: dict = {}
    body: Optional[WorkflowExecutionReq] = None
    status = 500
    error_msg = None

    try:
        await websocket.accept()
        data = await websocket.receive_json()
        login_user = data.pop("login_user", None) or {}
        data.pop("Authorization", None)
        data.pop("authorization", None)
        # get_login_user/get_user_id 读取 scope["login_user"]，写回即可复用原口径
        websocket.scope["login_user"] = login_user
        body = WorkflowExecutionReq(**data)
        await WorkflowExecutionService.execute_sync(
            websocket, workflow_id, body, user_id=await get_user_id(websocket))
        status = 200
        await _safe_send_json(websocket, {"code": status, "message": "执行成功"})
    except Exception as e:  # noqa: BLE001
        error_msg = str(e)[:2000]
        await _safe_send_json(websocket,
                              {"code": status, "message": f"执行过程中发生错误:{str(e)}"})
    finally:
        # 先完成关闭握手再落审计，保证对端拿到正常 close 帧
        await _safe_close(websocket)
        _audit_ws_run(websocket, f"/workflows/{workflow_id}/execute-sync",
                      {"workflowId": workflow_id,
                       "inputs": body.inputs if body else None},
                      login_user, start, status, error_msg)


@router.websocket("/{execution_id}/submit-sync")
async def submit_workflow_sync(websocket: WebSocket, execution_id: str):
    """同步(WebSocket)再提交:暂停后恢复 / 重新执行(需求 3)。

    与 execute-sync 同一套握手、鉴权与审计口径：首帧带 mode/inputs/approval 与网关注入
    的 login_user，执行事件经同一连接回推。预览页上审批人点「同意/不同意」走这个口，
    API/对话页则用异步的 POST /{execution_id}/submit。
    """
    start = time.perf_counter()
    login_user: dict = {}
    body: Optional[WorkflowSubmitReq] = None
    status = 500
    error_msg = None

    try:
        await websocket.accept()
        data = await websocket.receive_json()
        login_user = data.pop("login_user", None) or {}
        data.pop("Authorization", None)
        data.pop("authorization", None)
        websocket.scope["login_user"] = login_user
        body = WorkflowSubmitReq(**data)
        await WorkflowExecutionService.submit_sync(
            websocket, execution_id, body, user_id=await get_user_id(websocket))
        status = 200
        await _safe_send_json(websocket, {"code": status, "message": "执行成功"})
    except Exception as e:  # noqa: BLE001
        error_msg = str(e)[:2000]
        await _safe_send_json(websocket,
                              {"code": status, "message": f"提交失败:{str(e)}"})
    finally:
        await _safe_close(websocket)
        _audit_ws_run(websocket, f"/{execution_id}/submit-sync",
                      {"executionId": execution_id,
                       "mode": body.mode if body else None,
                       "inputs": body.inputs if body else None},
                      login_user, start, status, error_msg)


@router.get("/page", summary="分页查询执行历史")
@has_permission("workflow:execution:list")
async def page_executions(request: Request, page: int = 1, page_size: int = 10,
                          executionId: str = None, status: str = None):
    data = await WorkflowExecutionService.page_executions(
        current=page, size=page_size, execution_id=executionId, status=status)
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


@router.post("/{execution_id}/submit", summary="再提交（重新执行 / 审批后恢复，异步）")
async def submit_execution(request: Request, execution_id: str, body: WorkflowSubmitReq,
                           retry_times: int = Query(3, ge=1, le=10,
                                                     description="投递被 arq 拒绝时的重试次数")):
    """指定 executionId 再提交(需求 3)。

    mode=RETRY 全量重跑（终态可用）；mode=CONTINUE 审批后恢复（仅 PAUSED，必须带
    approval）。校验失败统一返回 400 与可读文案（未结束/画布漂移/无审批权限/结论不全）。
    鉴权：页面走 RBAC 权限位，第三方带 X-Workflow-Token 走 key ↔ 工作流归属校验。
    """
    try:
        user_id = await _auth_execution_control(request, execution_id)
        resp = await WorkflowExecutionService.submit(
            execution_id, body, user_id=user_id, retry_times=retry_times)
        return ApiResponse.success(data=resp, message="任务已提交")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    # 无登录态/权限不足/非法 key 由 UnauthorizedException 统一处理器转 401，不在这里抢


@router.post("/{execution_id}/cancel", summary="取消执行")
async def cancel_execution(request: Request, execution_id: str):
    """取消执行：RUNNING 下引擎在下一个节点检查点收尾，PAUSED 下直接写终态。

    也是「上次任务未结束」时的解套手段（提交接口的提示文案就让用户走这里）。
    """
    try:
        await _auth_execution_control(request, execution_id)
        ok = await WorkflowExecutionService.cancel(execution_id)
        if not ok:
            return ApiResponse.error(400, "执行不存在或已结束")
        return ApiResponse.success(message="已请求取消")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/{execution_id}/snapshot", summary="调试：获取执行快照（只读）")
@has_permission("workflow:execution:list")
async def get_execution_snapshot(request: Request, execution_id: str):
    data = await WorkflowExecutionService.get_snapshot(execution_id)
    if data is None:
        return ApiResponse.error(400, "执行记录不存在")
    return ApiResponse.success(data=data)


# ==================== 工作流维度执行历史 ====================
# 注意：/workflows/{workflow_id}/executions 是动态段嵌套，放在 /{execution_id} 之后
# 不影响匹配（execution_id 路径不带 /workflows 前缀段），但语义上必须两段匹配，安全。

@router.get("/workflows/{workflow_id}/executions", summary="工作流执行历史")
@has_permission("workflow:execution:list")
async def get_workflow_executions(request: Request, executionId: str = None,
                                  page: int = 1, page_size: int = 10, status: str = None):
    data = await WorkflowExecutionService.page_executions(
        current=page, size=page_size, execution_id=executionId, status=status)
    return ApiResponse.success(data=data)


# ==================== API Key ====================

api_key_router = APIRouter(prefix="/workflow-api-keys", tags=["工作流API Key"])


@api_key_router.post("", summary="创建 API Key（明文仅返回一次）")
@has_permission("workflow:apikey:add")
async def create_api_key(request: Request, body: ApiKeyCreateReq):
    try:
        resp = await WorkflowApiKeyService.create(
            body.workflowId, body.name, body.rateLimit or 0, body.expireDays,
            user_id=await get_user_id(request))
        return ApiResponse.success(data=resp, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@api_key_router.get("/workflows/{workflow_id}", summary="工作流的 API Key 列表")
@has_permission("workflow:apikey:list")
async def list_api_keys(request: Request, workflow_id: int):
    return ApiResponse.success(data=await WorkflowApiKeyService.list_by_workflow(workflow_id))


@api_key_router.put("/{api_key_id}", summary="编辑 API Key（名称/QPS/过期时间）")
@has_permission("workflow:apikey:edit")
async def update_api_key(request: Request, api_key_id: int, body: ApiKeyUpdateReq):
    # 只把请求体里真出现过的字段交下去，区分“没传”与“传了 null”（后者是改为永不过期）
    changes = {
        key: value
        for key, value in body.model_dump().items()
        if key in body.model_fields_set
    }
    ok = await WorkflowApiKeyService.update(api_key_id, changes)
    if not ok:
        return ApiResponse.error(400, "API Key 不存在")
    return ApiResponse.success(message="已保存")


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
