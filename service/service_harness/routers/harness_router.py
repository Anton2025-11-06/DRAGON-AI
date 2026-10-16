# -*- coding: utf-8 -*-
"""深度探索路由：会话管理 / 对话（ARQ 投递 + SSE 订阅）/ 模型可选清单。

权限口径（与首页沙箱 demo 路由 sandbox_router 一致）：任何登录用户可访问，
不绑定业务权限码，网关 JWT 中间件统一鉴权；资源归属在 service 里按
user_id 校验。菜单/权限点种子与 @has_permission 收口放 M3 加固一起做。

- POST /chat：建 turn（USER 行 + ASSISTANT 行 RUNNING）→ 投 arq harness
  流水线（job_id=turn_id 幂等）→ 返回 {turnId}；
- GET  /chat/{turn_id}/subscribe：SSE（EventSource 不能带自定义头，此端点
  不做权限装饰器，鉴权由网关完成——同 workflow 的 subscribe 口径）；
- POST /chat/{turn_id}/interrupt：DB 置 INTERRUPTED，worker 在事件间隙
  检查退出。

路由顺序注意：/page 为固定段，必须先于 /{session_id} 动态段声明。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from common.common_arq.queue import (
    PIPELINE_HARNESS, enqueue_job, next_split_number,
)
from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from common.common_permission.permission import get_login_user

from service.service_harness.schemas.harness_schema import (
    ChatReq, SessionCreateReq, SessionUpdateReq,
)
from service.service_harness.services.harness_pubsub import stream_turn_frames
from service.service_harness.services.options_service import (
    OPTIONS_KINDS, HarnessOptionsService,
)
from service.service_harness.services import sandbox_file_service 
from service.service_harness.services.session_service import HarnessSessionService

# arq 任务函数（worker_settings_harness.functions 里注册同一个模块路径）
HARNESS_TASK = "arq_tasks.tasks.harness.run_harness_turn"
# 投递被 arq 拒绝时的重试次数（对齐 workflow submit 的 retry_times 口径）
ENQUEUE_RETRY_TIMES = 3

router = APIRouter(prefix="/session", tags=["深度探索-会话"])


# ==================== 会话 ====================

@router.post("", summary="新建会话")
async def create_session(request: Request, body: SessionCreateReq):
    login_user = await get_login_user(request)
    data = await HarnessSessionService.create(login_user, body)
    return ApiResponse.success(data=data, message="创建成功")


@router.get("/page", summary="我的会话分页")
async def page_sessions(request: Request, current: int = Query(1, ge=1),
                        size: int = Query(10, ge=1, le=100),
                        title: Optional[str] = Query(None, max_length=128)):
    login_user = await get_login_user(request)
    data = await HarnessSessionService.page(login_user, current, size, title)
    return ApiResponse.success(data=data)


@router.get("/{session_id}", summary="会话详情")
async def get_session(request: Request, session_id: int):
    login_user = await get_login_user(request)
    data = await HarnessSessionService.detail(login_user, session_id)
    if data is None:
        return ApiResponse.error(400, "会话不存在")
    return ApiResponse.success(data=data)


@router.get("/{session_id}/messages", summary="会话消息历史")
async def get_messages(request: Request, session_id: int):
    login_user = await get_login_user(request)
    data = await HarnessSessionService.messages(login_user, session_id)
    if data is None:
        return ApiResponse.error(400, "会话不存在")
    return ApiResponse.success(data=data)


@router.patch("/{session_id}", summary="编辑会话（标题/归档）")
async def update_session(request: Request, session_id: int, body: SessionUpdateReq):
    login_user = await get_login_user(request)
    # 只把请求体里真出现过的字段交下去，区分「没传」与「传 null」
    changes = {key: value for key, value in body.model_dump().items()
               if key in body.model_fields_set}
    ok = await HarnessSessionService.update(login_user, session_id, changes)
    if not ok:
        return ApiResponse.error(400, "会话不存在")
    return ApiResponse.success(message="已保存")


@router.delete("/{session_id}", summary="删除会话（含全部消息）")
async def delete_session(request: Request, session_id: int):
    login_user = await get_login_user(request)
    ok = await HarnessSessionService.delete(login_user, session_id)
    if not ok:
        return ApiResponse.error(400, "会话不存在")
    return ApiResponse.success(message="删除成功")


# ==================== 对话 ====================

chat_router = APIRouter(prefix="/chat", tags=["深度探索-对话"])


@chat_router.post("", summary="发送消息（一轮对话，投递后经 SSE 订阅事件流）")
async def send_chat(request: Request, body: ChatReq):
    """建 turn 行 → 投 arq → 返回 turnId；前端拿到后 EventSource 订阅事件流。

    login_user（JWT 载荷 dict：user_id/username/roles/permissions）作为任务
    参数随 ARQ 投递——无 token 明文等敏感字段，worker 侧凭它做挂载 ACL 与
    KB 检索的鉴权上下文。
    """
    login_user = await get_login_user(request)
    try:
        turn = await HarnessSessionService.create_turn(
            login_user, body.sessionId, body.message, body.modelId, body.mounts)
    except ValueError as e:
        return ApiResponse.error(400, str(e))

    turn_id = turn["turnId"]
    try:
        for _ in range(max(1, ENQUEUE_RETRY_TIMES)):
            if await enqueue_job(
                    HARNESS_TASK, [turn_id, login_user],
                    job_id=turn_id,
                    split_number=await next_split_number(turn_id, PIPELINE_HARNESS),
                    pipeline=PIPELINE_HARNESS):
                return ApiResponse.success(data={"turnId": turn_id}, message="任务已提交")
            # job_id=turn_id 是新生成的 uuid4，重复只可能是极端竞态：稍等重投
            await asyncio.sleep(1)
    except Exception as e:  # noqa: BLE001
        log.error("harness chat enqueue failed turn={}: {}", turn_id, e)
        # 行已建但任务投不出去：回滚两行，避免页面留一条永远 RUNNING 的轮次
        await HarnessSessionService.delete_turn(turn_id)
        return ApiResponse.error(500, f"任务投递失败：{e}")
    await HarnessSessionService.delete_turn(turn_id)
    return ApiResponse.error(500, "任务投递失败（重复投递），请稍后重试")


@chat_router.get("/{turn_id}/subscribe", summary="SSE 订阅本轮事件流")
async def subscribe_chat(request: Request, turn_id: str):
    """终态回放 + 实时帧接到终态（流组织见 harness_pubsub.stream_turn_frames）。

    turn_id 是 uuid4 熵（同 workflow 的 executionId 口径）：EventSource 带不了
    自定义头，本端点不做权限装饰器，鉴权由网关完成。
    """
    return StreamingResponse(
        stream_turn_frames(turn_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx/网关代理缓冲，保证 token 实时推送
        })


@chat_router.post("/{turn_id}/interrupt", summary="中断本轮（事件边界生效）")
async def interrupt_chat(request: Request, turn_id: str):
    """RUNNING → INTERRUPTED：worker 在事件循环间隙检查该状态即收尾退出。"""
    login_user = await get_login_user(request)
    ok = await HarnessSessionService.interrupt_turn(login_user, turn_id)
    if not ok:
        return ApiResponse.error(400, "轮次不存在、不属于你或已结束")
    return ApiResponse.success(message="已请求中断")


# ==================== 模型可选清单 ====================

models_router = APIRouter(prefix="/models", tags=["深度探索-模型"])


@models_router.get("", summary="可选模型清单（支持工具调用 + 本人授权标注）")
async def list_models(request: Request):
    login_user = await get_login_user(request)
    data = await HarnessSessionService.usable_models(login_user)
    return ApiResponse.success(data={"items": data, "total": len(data)})


# ==================== 能力挂载候选 ====================

options_router = APIRouter(prefix="/options", tags=["深度探索-能力挂载"])


@options_router.get("/{kind}", summary="挂载候选清单（use 口径：自建 ∪ 授权）")
async def list_options(request: Request, kind: str):
    """kind ∈ kb|mcp|tool|skill：知识库/MCP连接/平台工具/技能。

    四口统一返回平铺列表（id/name/description，skill 另带 code、mcp 另带
    type），见 options_service 的口径说明；执行侧 ACL 由 worker 桥再卡一道。
    """
    login_user = await get_login_user(request)
    if kind not in OPTIONS_KINDS:
        return ApiResponse.error(400, f"未知能力类型: {kind}")
    try:
        data = await HarnessOptionsService.options(login_user, kind)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data={"items": data, "total": len(data)})


# ==================== 沙箱文件（OpenSandbox 工作区代理，M3-4） ====================

sandbox_router = APIRouter(prefix="/sandbox", tags=["深度探索-沙箱文件"])


@sandbox_router.get("/files", summary="沙箱工作区目录列表")
async def harness_sandbox_files(request: Request, path: str = sandbox_file_service.WORKSPACE_ROOT,
                               session_id: str = ""):
    """每用户独立云沙箱（harness-{user_id}），文件跨会话保留。

    首次访问若沙箱从未建过会触发分钟级 bootstrap；正常路径是先对话后浏览
    （worker 已建沙箱，这里走 reattach+resume 快路径）。响应形状与旧
    /api/workflow/sandbox/files 一致，前端零改动切换。
    """
    login_user = await get_login_user(request)
    try:
        data = await sandbox_file_service.list_dir(login_user, path, session_id)
        return ApiResponse.success(data=data)
    except sandbox_file_service.HarnessSandboxError as e:
        return ApiResponse.error(400, str(e))


@sandbox_router.get("/file", summary="查看沙箱文件内容")
async def harness_sandbox_file(request: Request, path: str = sandbox_file_service.WORKSPACE_ROOT,
                              session_id: str = ""):
    """文本截断预览；二进制只回标记（内容走下载口）。"""
    login_user = await get_login_user(request)
    try:
        data = await sandbox_file_service.read_text_file(login_user, path, session_id)
        return ApiResponse.success(data=data)
    except sandbox_file_service.HarnessSandboxError as e:
        return ApiResponse.error(400, str(e))


@sandbox_router.post("/upload", summary="上传文件到沙箱工作区")
async def harness_sandbox_upload(request: Request, path: str = Form(sandbox_file_service.WORKSPACE_ROOT),
                                 session_id: str = Form(""),
                                 file: UploadFile = File(...)):
    """base64 分块写入（跟 agentscope 快照同款通道，单次≤20MB）。"""
    login_user = await get_login_user(request)
    content = await file.read()
    try:
        data = await sandbox_file_service.save_file(login_user, path, file.filename or "",
                                                   content, session_id)
        return ApiResponse.success(data=data, message="上传成功")
    except sandbox_file_service.HarnessSandboxError as e:
        return ApiResponse.error(400, str(e))


@sandbox_router.get("/download", summary="下载沙箱文件或目录(zip)")
async def harness_sandbox_download(request: Request, path: str = sandbox_file_service.WORKSPACE_ROOT,
                                  session_id: str = ""):
    import urllib.parse

    login_user = await get_login_user(request)
    try:
        data, name, is_dir = await sandbox_file_service.download(login_user, path, session_id)
    except sandbox_file_service.HarnessSandboxError as e:
        return ApiResponse.error(400, str(e))
    quoted = urllib.parse.quote(name)
    return Response(
        content=data,
        media_type="application/zip" if is_dir else "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


