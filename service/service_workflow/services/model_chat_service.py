# -*- coding: utf-8 -*-
"""模型对话会话/消息服务（service_workflow，SQLAlchemy ORM）

对话调用由前端直连网关 /api/model（api-key 鉴权，X-User-Api-Key 头）；
会话/消息经网关正常 token 认证（Authorization 头）后注入 X-User-Token 转发
本服务；本服务按 user_id 强隔离（仅本人可见/可改/可删）。
表结构：tb_model_chat_session / tb_model_chat_message（与模型广场建表脚本一致）。
"""
from typing import List

from sqlalchemy import delete, select

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.models.model_chat import ModelChatMessage, ModelChatSession
from service.service_workflow.schemas.model_chat_schema import (
    ChatMessageItem, ChatSessionCreateReq, ChatSessionUpdateReq,
)

# 会话标题自动截断长度（取首条用户消息前 30 字）
TITLE_MAX = 30


def _fmt(dt) -> str:
    """datetime → 前端展示格式 YYYY-MM-DD HH:MM:SS"""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


class ModelChatService:
    """模型对话：会话与消息管理"""

    @staticmethod
    async def create_session(user_id: int, body: ChatSessionCreateReq) -> int:
        """新建会话（携带模型/偏好快照，切换会话时恢复上下文）"""
        async with mysql_client.get_session() as session:
            m = ModelChatSession(
                user_id=user_id,
                title=(body.title or "新会话").strip() or "新会话",
                model_apply_id=body.model_apply_id or 0,
                model_name=body.model_name or None,
                reasoning=1 if body.reasoning else 0,
                stream=1 if body.stream else 0,
            )
            session.add(m)
            await session.commit()
            await session.refresh(m)
            log.info(f"ModelChat session created: id={m.id} user={user_id}")
            return m.id

    @staticmethod
    async def list_sessions(user_id: int) -> list:
        """我的会话列表（最近更新优先，最多 20 条）"""
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(ModelChatSession)
                .where(ModelChatSession.user_id == user_id)
                .order_by(ModelChatSession.update_time.desc(), ModelChatSession.id.desc())
                .limit(20))).scalars().all()
            return [{
                "id": m.id, "title": m.title, "model_apply_id": m.model_apply_id,
                "model_name": m.model_name,
                "reasoning": bool(m.reasoning), "stream": bool(m.stream),
                "create_time": _fmt(m.create_time), "update_time": _fmt(m.update_time),
            } for m in rows]

    @staticmethod
    async def update_session(session_id: int, user_id: int,
                             body: ChatSessionUpdateReq) -> bool:
        """更新会话（仅更新传入字段；非本人会话视为不存在）"""
        async with mysql_client.get_session() as session:
            m = (await session.execute(
                select(ModelChatSession)
                .where(ModelChatSession.id == session_id,
                       ModelChatSession.user_id == user_id))).scalar_one_or_none()
            if not m:
                return False
            if body.title is not None:
                m.title = (body.title or "").strip() or "新会话"
            if body.model_apply_id is not None:
                m.model_apply_id = body.model_apply_id or 0
            if body.model_name is not None:
                m.model_name = body.model_name or None
            if body.reasoning is not None:
                m.reasoning = 1 if body.reasoning else 0
            if body.stream is not None:
                m.stream = 1 if body.stream else 0
            await session.commit()
            log.info(f"ModelChat session updated: id={session_id} user={user_id}")
            return True

    @staticmethod
    async def delete_session(session_id: int, user_id: int) -> bool:
        """删除会话（级联删除消息；非本人会话视为不存在）"""
        async with mysql_client.get_session() as session:
            owned = (await session.execute(
                select(ModelChatSession.id)
                .where(ModelChatSession.id == session_id,
                       ModelChatSession.user_id == user_id))).scalar_one_or_none()
            if owned is None:
                return False
            await session.execute(delete(ModelChatMessage).where(
                ModelChatMessage.session_id == session_id))
            await session.execute(delete(ModelChatSession).where(
                ModelChatSession.id == session_id))
            await session.commit()
            log.info(f"ModelChat session deleted: id={session_id} user={user_id}")
            return True

    @staticmethod
    async def list_messages(session_id: int, user_id: int) -> list:
        """会话消息列表（按时间正序；非本人会话返回 None 由调用方转 404）"""
        async with mysql_client.get_session() as session:
            owned = (await session.execute(
                select(ModelChatSession.id)
                .where(ModelChatSession.id == session_id,
                       ModelChatSession.user_id == user_id))).scalar_one_or_none()
            if owned is None:
                return None
            rows = (await session.execute(
                select(ModelChatMessage)
                .where(ModelChatMessage.session_id == session_id)
                .order_by(ModelChatMessage.id.asc()))).scalars().all()
            return [{
                "id": msg.id, "role": msg.role, "content": msg.content or "",
                "reasoning_content": msg.reasoning_content or "",
                "model_name": msg.model_name,
                "create_time": _fmt(msg.create_time),
            } for msg in rows]

    @staticmethod
    async def save_messages(session_id: int, user_id: int,
                            messages: List[ChatMessageItem]) -> int:
        """批量保存消息（user + assistant 一并提交）；首次用户消息自动生成会话标题。
        返回保存条数；会话不存在或非本人返回 -1。"""
        if not messages:
            return 0
        async with mysql_client.get_session() as session:
            m = (await session.execute(
                select(ModelChatSession)
                .where(ModelChatSession.id == session_id,
                       ModelChatSession.user_id == user_id))).scalar_one_or_none()
            if not m:
                return -1
            for item in messages:
                session.add(ModelChatMessage(
                    session_id=session_id,
                    role=item.role,
                    content=item.content or None,
                    reasoning_content=item.reasoning_content or None,
                    model_name=item.model_name or None))
            # 会话标题仍为默认值且本批含用户消息时，取首条用户消息前 30 字
            if m.title == "新会话":
                first_user = next(
                    (x for x in messages
                     if x.role == "USER" and x.content), None)
                if first_user:
                    title = str(first_user.content).strip().replace("\n", " ")
                    title = title[:TITLE_MAX] + ("..." if len(title) > TITLE_MAX else "")
                    m.title = title
            await session.commit()
            return len(messages)