# -*- coding: utf-8 -*-
"""模型对话会话/消息管理（service_workflow）：/sessions*

- 前端经网关 /api/workflow/sessions* 转发：网关 TokenCheckMiddleware 校验
  Authorization: Bearer <登录 token> 后注入内部令牌（X-User-Token，仅含 user_id）
- 本服务经 get_login_user 解析用户身份，按 user_id 强隔离（非本人数据返回 404）
"""

from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user
from service.service_workflow.schemas.model_chat_schema import ChatSessionCreateReq, ChatSessionUpdateReq, \
    ChatMessageSaveReq
from service.service_workflow.services.model_chat_service import ModelChatService

router = APIRouter(prefix="", tags=["模型对话"])


async def _current_user_id(request: Request) -> int:
    """当前用户 id：网关注入的内部令牌（X-User-Token）→ get_login_user 载荷"""
    login_user = await get_login_user(request)
    uid = (login_user or {}).get("user_id")
    if not uid:
        from common.common_middleware.exception_handler import UnauthorizedException
        raise UnauthorizedException("未登录，禁止操作！")
    return uid


@router.get("/sessions", summary="我的会话列表")
async def list_sessions(request: Request):
    user_id = await _current_user_id(request)
    data = await ModelChatService.list_sessions(user_id)
    return ApiResponse.success(data=data)


@router.post("/sessions", summary="新建会话（携带模型/偏好快照）")
async def create_session(body: ChatSessionCreateReq, request: Request):
    user_id = await _current_user_id(request)
    session_id = await ModelChatService.create_session(user_id, body)
    return ApiResponse.success(data={"id": session_id}, message="创建成功")


@router.put("/sessions/{session_id}", summary="更新会话（标题/模型/偏好）")
async def update_session(session_id: int, body: ChatSessionUpdateReq, request: Request):
    user_id = await _current_user_id(request)
    ok = await ModelChatService.update_session(session_id, user_id, body)
    if not ok:
        return ApiResponse.error(404, "会话不存在")
    return ApiResponse.success(message="更新成功")


@router.delete("/sessions/{session_id}", summary="删除会话（级联删除消息）")
async def delete_session(session_id: int, request: Request):
    user_id = await _current_user_id(request)
    if not await ModelChatService.delete_session(session_id, user_id):
        return ApiResponse.error(404, "会话不存在")
    return ApiResponse.success(message="删除成功")


@router.get("/sessions/{session_id}/messages", summary="会话消息列表")
async def list_messages(session_id: int, request: Request):
    user_id = await _current_user_id(request)
    data = await ModelChatService.list_messages(session_id, user_id)
    if data is None:
        return ApiResponse.error(404, "会话不存在")
    return ApiResponse.success(data=data)


@router.post("/sessions/{session_id}/messages", summary="批量保存会话消息")
async def save_messages(session_id: int, body: ChatMessageSaveReq, request: Request):
    user_id = await _current_user_id(request)
    count = await ModelChatService.save_messages(session_id, user_id, body.messages)
    if count < 0:
        return ApiResponse.error(404, "会话不存在")
    return ApiResponse.success(data={"saved": count}, message="保存成功")
