# -*- coding: utf-8 -*-
"""工作流执行域路由:提交（唯一动作） / SSE 订阅 / 控制 / API Key / 文件上传。

对外只有一个提交入口，新建执行与操作已有执行都走它：
- POST /workflow-executions/submit → {executionId, status, pauseGeneration,
  duplicated, pendingApprovals}；不带 executionId 即新建一条执行
- WS   /workflow-executions/submit → 同样一份入参（首帧），事件经同一连接回推，
  预览页的运行与审批人都走这个口
- GET  /workflow-executions/{executionId}/subscribe → SSE（EventSource 不能带自定义
  头，此端点不做权限装饰器，鉴权由网关完成）
- POST /workflow-executions/{executionId}/cancel → 取消由 API 改执行记录表状态，
  引擎每节点前查库生效

暂停只能由审批节点触发，恢复与重跑全部走 submit。契约见
/docs/workflow-execution-contract.md，设计冻结见 docs/workflow-approval-memory.md。

workflow-files 只做 HTTP 形参适配，存储能力全在 common.common_storage（ upload/download/
delete/exists），不落库：文件名（{uuid}_{原名}）就是唯一标识，上传返回的 URL 可匿名访问。

路由顺序注意:/submit 与 /page 为固定段,必须先于 /{execution_id} 动态段声明。
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
from common.common_utils.ip_util import get_client_ip
from common.common_middleware.exception_handler import UnauthorizedException
from service.service_workflow.execution.submit_resolver import SubmitRejected
from service.service_workflow.schemas.workflow_schema import (
    ApiKeyCreateReq, ApiKeyUpdateReq, WorkflowSubmitReq,
)
from service.service_workflow.services.event_pubsub import (
    start_ws_event_channel, stop_ws_event_channel,
)
from service.service_workflow.services.workflow_apikey_service import WorkflowApiKeyService
from service.service_workflow.services.workflow_execution_service import WorkflowExecutionService

router = APIRouter(prefix="/workflow-executions", tags=["工作流执行"])


# ==================== 固定路径(先声明) ====================
@router.post("/submit", summary="提交执行（新建 / 答复审批 / 重开，唯一入口）")
@has_permission("workflow:execution:run")
async def submit_execution(request: Request, body: WorkflowSubmitReq,
                           retry_times: int = Query(3, ge=1, le=10,
                                                    description="投递被 arq 拒绝时的重试次数")):
    """把一次提交落到执行行上并投到 arq 队列，立即返回这一行的现状。

    提交意图只看给了哪几个键：不带 executionId 即新建，带 decisions 即答复审批，
    带 restart 即放弃现状重跑。不允许的组合统一回 400 与可读文案（校验矩阵
    在 submit_resolver）。
    """
    try:
        if request.headers.__contains__("X-Workflow-Token"):
            await WorkflowExecutionService.check_api_key_scope(body.executionId,
                                                               request.headers.get("X-Workflow-Token"))

        result = await WorkflowExecutionService.submit(
            body,
            user_id=get_client_ip(request) if request.headers.__contains__("X-Workflow-Token") else await get_user_id(
                request),
            trigger_type="API" if request.headers.__contains__("X-Workflow-Token") else "DEBUG",
            retry_times=retry_times
        )
        return ApiResponse.success(data=result,
                                   message="该审批已提交，请勿重复审批" if result.get("duplicated") else "任务已提交")
    except SubmitRejected as e:
        return ApiResponse.error(e.code, str(e))
    except ValueError as e:
        # 行已抢成待跑但投不出去（上一轮还在收尾）：现场已回滚，这里只说清原因
        return ApiResponse.error(400, str(e))


@router.websocket("/submit")
async def submit_execution_ws(websocket: WebSocket):
    """WS 提交：读首帧入参 → 本进程跑 → 事件经同一连接回推 → 关闭。

    预览页的运行与审批人点「同意/不同意」都走这个口，差别只在首帧给了哪几个键。
    浏览器 WS 无法带 Authorization 头，登录态由网关解析后随首帧 login_user 下发；
    TokenCheck/OperateLog 等 BaseHTTPMiddleware 对 websocket scope 不生效，故此处
    把 login_user 写回 scope（供 get_user_id 复用）并手动落审计日志。
    """
    start = time.perf_counter()
    login_user: dict = {}
    body: Optional[WorkflowSubmitReq] = None
    status = 500
    error_msg = None
    result: dict = {}

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

    async def _send_reject(websocket: WebSocket, code: int, message: str) -> None:
        """把一次没被允许的提交回给对端：业务码 + 面向调用方的原文案。

        与 HTTP 侧同一个口径：这类回执不是「提交失败」（它根本没开始跑），不套前缀、
        不报 500，否则页面上只能看到一句无法归类的服务器错误。
        """
        await stop_ws_event_channel(websocket)
        await _safe_send_json(websocket, {"code": code, "message": message})

    try:
        await websocket.accept()
        # 事件回推走串行通道：引擎 emit 只入队，绝不再 await 传输（否则对端不读会把
        # 执行卡在「已挂起但收不了尾」的状态，见 event_pubsub._WsEventSender）
        start_ws_event_channel(websocket)
        data = await websocket.receive_json()
        login_user = data.pop("login_user", None) or {}
        data.pop("Authorization", None)
        data.pop("authorization", None)
        # get_login_user/get_user_id 读取 scope["login_user"]，写回即可复用原口径
        websocket.scope["login_user"] = login_user
        body = WorkflowSubmitReq(**data)
        result = await WorkflowExecutionService.submit(
            body, websocket=websocket, user_id=await get_user_id(websocket),
            trigger_type="DEBUG")
        status = 200
        await stop_ws_event_channel(websocket)
        await _safe_send_json(websocket, {"code": status, "message": "提交成功",
                                          "data": result})
    except SubmitRejected as e:
        # 校验没过、执行现状不允许、抢占输给并发：业务码与文案直接回给对端
        status, error_msg = e.code, str(e)
        await _send_reject(websocket, e.code, str(e))
    except ValueError as e:
        # 行已抢成待跑但驱动不起来（投递被队列拒绝且重试用尽）：现场已回滚
        status, error_msg = 400, str(e)
        await _send_reject(websocket, 400, str(e))
    except Exception as e:  # noqa: BLE001
        error_msg = str(e)[:2000]
        await stop_ws_event_channel(websocket)
        await _safe_send_json(websocket,
                              {"code": status, "message": f"提交失败:{str(e)}"})
    finally:
        await stop_ws_event_channel(websocket)

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

        # 先完成关闭握手再落审计，保证对端拿到正常 close 帧
        await _safe_close(websocket)

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

        _audit_ws_run(websocket, "/submit",
                      {"executionId": body.executionId if body else None,
                       "workflowId": body.workflowId if body else None,
                       "values": body.values if body else None},
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
    """这条执行此刻的事实：状态、代次、pendingApprovals。

    事件帧不承载审批内容，approvalToken 只在这里给，因此详情也是 API Key 调用方的
    必读口（重连、晚订阅、事后补答都只能回到这里取待办）。
    """
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


@router.post("/{execution_id}/cancel", summary="取消执行")
@has_permission("workflow:execution:cancel")
async def cancel_execution(request: Request, execution_id: str):
    """取消执行：RUNNING 下引擎在下一个节点检查点收尾，PAUSED 下直接写终态。

    也是「上次任务未结束」时的解套手段（提交接口的提示文案就让用户走这里）。
    """
    try:
        if request.headers.__contains__("X-Workflow-Token"):
            await WorkflowExecutionService.check_api_key_scope(execution_id,
                                                               request.headers.get("X-Workflow-Token"))
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
