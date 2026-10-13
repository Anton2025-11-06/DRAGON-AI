# -*- coding: utf-8 -*-
"""深度探索会话/消息/轮次服务（tb_harness_session / tb_harness_message 的唯一读写口）。

口径（与 tb_model_chat_* 的会话范式一致，差异只在「按轮组织」）：
- 归属：会话行 user_id 是唯一信任边界——所有读写先过归属校验（ADMIN 不放行
  他人会话：对话内容可能含隐私，跨用户可见没有业务理由）；
- 一轮对话 = USER 行 + ASSISTANT 行同 turn_id：turn_id（uuid4）同时是 arq
  job_id 与事件频道后缀，幂等投递、发布订阅、SSE 回放共用这一个键；
- ASSISTANT 行是轮次状态权威（RUNNING → COMPLETED/FAILED/INTERRUPTED），
  worker 终态经 finish_turn 回写；USER 行只作内容回显，状态恒 COMPLETED；
- 跨进程交接只有 MySQL 这两行：API 进程写下「本轮要什么」，arq worker 读它
  跑一轮再写回（与 workflow 执行行同一个模式）。

注意：离开 mysql session 前必须把要用的字段拷进局部变量/dict——session 关闭
后 ORM 属性不可读（workflow_execution_service 同款坑）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import delete, func, select

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

from service.service_harness.models.harness_entity import (
    SESSION_ACTIVE, HarnessMessage, HarnessSession,
    TURN_COMPLETED, TURN_FAILED, TURN_INTERRUPTED, TURN_RUNNING,
)
from service.service_harness.schemas.harness_schema import (
    MountsReq, SessionCreateReq,
)

# 消息对外的角色常量（与 tb_model_chat_message 的 role 口径一致）
ROLE_USER = "USER"
ROLE_ASSISTANT = "ASSISTANT"


def _session_dto(row: HarnessSession) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "createTime": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else None,
        "updateTime": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if row.update_time else None,
    }


def _message_dto(row: HarnessMessage) -> dict:
    return {
        "id": row.id,
        "sessionId": row.session_id,
        "turnId": row.turn_id,
        "role": row.role,
        "content": row.content,
        "thinking": row.thinking,
        "toolCalls": row.tool_calls or [],
        "status": row.status,
        "error": row.error,
        "modelName": row.model_name,
        # 本轮模型与挂载（前端切会话时按最后一轮回显）
        "modelId": row.model_id,
        "mounts": row.mounts or {},
        "createTime": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else None,
    }


class HarnessSessionService:
    """会话 CRUD + 轮次生命周期（路由与 worker 共用；worker 只用轮次那几个方法）。"""

    # ==================== 会话 ====================

    @staticmethod
    async def page(login_user: dict, current: int = 1, size: int = 10,
                   title: Optional[str] = None) -> dict:
        """我的会话分页（按 update_time 倒序：最近动过的排最前）。"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            stmt = select(HarnessSession).where(HarnessSession.user_id == user_id)
            if title:
                stmt = stmt.where(HarnessSession.title.like(f"%{title}%"))
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await session.execute(
                stmt.order_by(HarnessSession.update_time.desc())
                .offset((current - 1) * size).limit(size))).scalars().all()
            records = [_session_dto(r) for r in rows]
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    async def create(login_user: dict, req: SessionCreateReq) -> dict:
        """新建会话（只落标题）：模型与挂载不绑会话，发消息时逐轮携带。"""
        user_id = int(login_user.get("user_id") or 0)
        row = HarnessSession(
            user_id=user_id, title=req.title, status=SESSION_ACTIVE)
        async with mysql_client.get_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _session_dto(row)

    @staticmethod
    async def detail(login_user: dict, session_id: int) -> Optional[dict]:
        """会话详情"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = await session.get(HarnessSession, session_id)
            if row is None or row.user_id != user_id:
                return None
            return _session_dto(row)

    @staticmethod
    async def update(login_user: dict, session_id: int, changes: dict) -> bool:
        """编辑会话"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = await session.get(HarnessSession, session_id)
            if row is None or row.user_id != user_id:
                return False
            if "title" in changes:
                row.title = changes["title"]
            if "status" in changes:
                row.status = changes["status"]
            await session.commit()
            return True

    @staticmethod
    async def delete(login_user: dict, session_id: int) -> bool:
        """删除会话及其全部消息（AgentState 留在 Redis 由过期策略自然回收）。"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = await session.get(HarnessSession, session_id)
            if row is None or row.user_id != user_id:
                return False
            await session.execute(delete(HarnessMessage)
                                  .where(HarnessMessage.session_id == session_id))
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def messages(login_user: dict, session_id: int) -> Optional[list[dict]]:
        """会话历史（按 id 平铺序：USER/ASSISTANT 交替，一轮两行）。"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            row = await session.get(HarnessSession, session_id)
            if row is None or row.user_id != user_id:
                return None
            rows = (await session.execute(
                select(HarnessMessage).where(HarnessMessage.session_id == session_id)
                .order_by(HarnessMessage.id))).scalars().all()
            return [_message_dto(r) for r in rows]

    # ==================== 模型可选清单 ====================

    @staticmethod
    async def usable_models(login_user: dict) -> list[dict]:
        """本人已授权、且可用于深度探索的模型清单（启用 + 支持工具调用）。

        口径：只返回本人审批通过（apply status=1）的模型——可选即能用，前端把
        model_id 直接传给 chat，worker 按 model_id 命中 Redis 配置，不再逐轮回查授权。
        深度探索是 ReAct 智能体，模型必须支持 function call，否则工具循环根本
        转不起来——这里按 supports_function_call 过滤而不是让运行时报错。
        """
        from service.service_system.models.model import Model, ModelApply

        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(Model.id, Model.name, Model.provider, Model.model_name,
                       Model.category)
                .join(ModelApply, ModelApply.model_id == Model.id)
                .where(ModelApply.user_id == user_id,
                       ModelApply.status == 1,
                       Model.status == 1,
                       Model.supports_function_call == 1)
                .distinct()
                .order_by(Model.id)
            )).all()
        return [{
            "id": r.id, "name": r.name, "provider": r.provider,
            "modelName": r.model_name, "category": r.category,
        } for r in rows]

    # ==================== 轮次（turn） ====================

    @staticmethod
    async def create_turn(login_user: dict, session_id: int, message: str,
                          model_id: int,
                          mounts: Optional[MountsReq]) -> dict:
        """开一轮对话：USER 行 + ASSISTANT 行（RUNNING），返回投递要用的全部事实。

        本轮的模型与挂载都落在消息行上（同一会话里逐轮可切换），worker 从
        ASSISTANT 行取它们跑本轮。模型的授权在可选清单接口已收口（这里不再回查），
        挂载的 ACL 校验在 worker 侧（ensure_action）做——这里是「本轮想用什么」
        的记录口，不是信任边界。
        :raises ValueError: 会话不存在 / 不属于本人 / 已归档
        """
        user_id = int(login_user.get("user_id") or 0)
        turn_id = str(uuid4())
        effective = mounts.model_dump() if mounts is not None else {}
        mid = int(model_id or 0)
        async with mysql_client.get_session() as session:
            row = await session.get(HarnessSession, session_id)
            if row is None or row.user_id != user_id:
                raise ValueError("会话不存在")
            if row.status != SESSION_ACTIVE:
                raise ValueError("会话已归档，请先恢复再发消息")
            session.add_all([
                HarnessMessage(session_id=session_id, turn_id=turn_id, role=ROLE_USER,
                               content=message, status=TURN_COMPLETED,
                               model_id=mid, mounts=effective),
                HarnessMessage(session_id=session_id, turn_id=turn_id, role=ROLE_ASSISTANT,
                               status=TURN_RUNNING,
                               model_id=mid, mounts=effective),
            ])
            await session.commit()
            return {"turnId": turn_id, "sessionId": session_id,
                    "modelId": mid, "mounts": effective}

    @staticmethod
    async def turn_snapshot(turn_id: str) -> Optional[dict]:
        """轮次事实快照（worker 侧恢复运行时用：归属、挂载、模型授权）。"""
        async with mysql_client.get_session() as session:
            msg = (await session.execute(
                select(HarnessMessage).where(HarnessMessage.turn_id == turn_id,
                                             HarnessMessage.role == ROLE_ASSISTANT)
            )).scalar_one_or_none()
            if msg is None:
                return None
            sess = await session.get(HarnessSession, msg.session_id)
            if sess is None:
                return None
            return {
                "turnId": turn_id, "sessionId": sess.id, "userId": sess.user_id,
                "modelId": msg.model_id, "modelName": msg.model_name,
                "mounts": msg.mounts or {}, "status": msg.status,
                "message": (await session.execute(
                    select(HarnessMessage.content)
                    .where(HarnessMessage.turn_id == turn_id,
                           HarnessMessage.role == ROLE_USER)
                )).scalar_one_or_none(),
            }

    @staticmethod
    async def turn_status(turn_id: str) -> Optional[str]:
        """轮次状态（worker 事件循环里的中断检查口，只查一列不取整行）。"""
        async with mysql_client.get_session() as session:
            return await session.scalar(
                select(HarnessMessage.status)
                .where(HarnessMessage.turn_id == turn_id,
                       HarnessMessage.role == ROLE_ASSISTANT))

    @staticmethod
    async def delete_turn(turn_id: str) -> None:
        """按 turn_id 删两行（仅投递失败回滚用：创建成功但任务投不出去）。"""
        async with mysql_client.get_session() as session:
            await session.execute(delete(HarnessMessage)
                                  .where(HarnessMessage.turn_id == turn_id))
            await session.commit()

    @staticmethod
    async def interrupt_turn(login_user: dict, turn_id: str) -> bool:
        """请求中断：RUNNING → INTERRUPTED（worker 在事件间隙检查退出）。

        归属校验经所属会话（turn 行只有 session_id）：不是本人的轮次按
        「不存在」处理，不区分文案避免探测他人 turn_id。
        """
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            msg = (await session.execute(
                select(HarnessMessage).where(HarnessMessage.turn_id == turn_id,
                                             HarnessMessage.role == ROLE_ASSISTANT)
            )).scalar_one_or_none()
            if msg is None:
                return False
            sess = await session.get(HarnessSession, msg.session_id)
            if sess is None or sess.user_id != user_id:
                return False
            if msg.status != TURN_RUNNING:
                return False
            msg.status = TURN_INTERRUPTED
            msg.error = "用户请求中断"
            await session.commit()
            return True

    @staticmethod
    async def finish_turn(turn_id: str, status: str, *,
                          content: Optional[str] = None,
                          thinking: Optional[str] = None,
                          tool_calls: Optional[list] = None,
                          error: Optional[str] = None,
                          model_name: Optional[str] = None) -> None:
        """轮次终态回写（worker 专用：reply_stream 收尾后调用）。

        只写仍未终态的行：同一 turn 被重复消费（幂等投递残留/重试）时以先到
        的终态为准，后到的直接跳过，不会把 COMPLETED 改回 FAILED。
        """
        async with mysql_client.get_session() as session:
            msg = (await session.execute(
                select(HarnessMessage).where(HarnessMessage.turn_id == turn_id,
                                             HarnessMessage.role == ROLE_ASSISTANT)
            )).scalar_one_or_none()
            if msg is None or msg.status in (TURN_COMPLETED, TURN_FAILED,
                                             TURN_INTERRUPTED):
                log.warning("harness finish_turn skipped: turn={} status={}",
                            turn_id, msg.status if msg else "missing")
                return
            msg.status = status
            if model_name is not None:
                msg.model_name = model_name
            if content is not None:
                msg.content = content
            if thinking is not None:
                msg.thinking = thinking
            if tool_calls is not None:
                msg.tool_calls = tool_calls
            msg.error = error
            await session.commit()
