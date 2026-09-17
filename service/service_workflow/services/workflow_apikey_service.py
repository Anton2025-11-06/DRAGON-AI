# -*- coding: utf-8 -*-
"""工作流 API Key 服务：创建 / 列表 / 状态 / 删除 / 校验。

Key 格式 `wf_` + 32 位 urlsafe 随机串；不做脱敏（需求：生成后可随时查看完整内容）。
供第三方以 API 触发方式调用已发布工作流（对应前端 workflow-api-keys 契约）；
增删改同步到 Redis（gateway_router 读取鉴权 + QPS 限流，需求：鉴权限流在网关处执行）。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, select

from common.common_constants.constant import PREFIX_WORKFLOW_API_KEY
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client
from service.service_workflow.models.workflow_entity import WorkflowApiKey


async def _sync_redis_config(api_key: str, config: dict) -> None:
    """API Key 配置同步 Redis（网关鉴权/限流的读取源），失败仅告警不影响主链路。"""
    try:
        await client.hset(PREFIX_WORKFLOW_API_KEY,
                          {api_key: json.dumps(config, ensure_ascii=False)})
    except Exception as e:  # noqa: BLE001
        log.error("sync workflow api key redis failed: {}", e)


def _row_config(row: WorkflowApiKey) -> dict:
    """行 → 网关读取的那份配置（create/update/update_status 共用同一口径）。

    网关是拿 expireTime 字符串按 "%Y-%m-%d %H:%M:%S" 比对的，格式变了会直接判成无效 Key。
    """
    return {
        "workflowId": row.workflow_id,
        "rateLimit": row.rate_limit,
        "expireTime": row.expire_time.strftime("%Y-%m-%d %H:%M:%S") if row.expire_time else None,
        "status": row.status,
    }


class WorkflowApiKeyService:

    @staticmethod
    async def create(workflow_id: int, name: str, rate_limit: int = 0,
                     expire_days: Optional[int] = None, user_id: int = 0) -> dict:
        api_key = "wf_" + secrets.token_urlsafe(24)
        expire_time = datetime.now() + timedelta(days=expire_days) if expire_days else None
        async with mysql_client.get_session() as session:
            row = WorkflowApiKey(workflow_id=workflow_id, name=name, api_key=api_key,
                                 rate_limit=rate_limit, expire_time=expire_time,
                                 status="ACTIVE")
            session.add(row)
            await session.commit()
            # 前端 ApiKeyCreateResp：id / apiKey（明文）/ name / workflowId
            await _sync_redis_config(api_key, _row_config(row))
            return {"id": row.id, "apiKey": api_key, "name": row.name,
                    "workflowId": row.workflow_id}

    @staticmethod
    async def list_by_workflow(workflow_id: int) -> list[dict]:
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(WorkflowApiKey).where(WorkflowApiKey.workflow_id == workflow_id)
                .order_by(WorkflowApiKey.id.desc()))).scalars().all()
            return [{
                "id": r.id, "name": r.name, "apiKey": r.api_key,
                "status": r.status, "rateLimit": r.rate_limit,
                "expireTime": r.expire_time.strftime("%Y-%m-%d %H:%M:%S") if r.expire_time else None,
                "lastUsedTime": r.last_used_time.strftime("%Y-%m-%d %H:%M:%S") if r.last_used_time else None,
                "totalCalls": r.total_calls,
                "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
            } for r in rows]

    @staticmethod
    async def update(api_key_id: int, changes: dict) -> bool:
        """编辑 API Key：备注名 / QPS / 过期时间，只改 changes 里传了的字段。

        QPS 与过期时间都参与网关判定，但网关只读 Redis，所以改完必须同步一份过去，
        否则会出现“页面改了、第三方调用仍按旧限流跑”。
        """
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowApiKey, api_key_id)
            if row is None:
                return False
            name = changes.get("name")
            if name:
                row.name = name
            rate_limit = changes.get("rateLimit")
            if rate_limit is not None:
                row.rate_limit = int(rate_limit)
            if "expireTime" in changes:
                row.expire_time = changes["expireTime"]
                # EXPIRED 是系统判定的中间态，不是用户主动停用，顺延/改永不过期后自动回可用
                if row.status == "EXPIRED" and (
                    row.expire_time is None or row.expire_time > datetime.now()
                ):
                    row.status = "ACTIVE"
            await session.commit()
            await _sync_redis_config(row.api_key, _row_config(row))
            return True

    @staticmethod
    async def update_status(api_key_id: int, status: str) -> bool:
        # 兼容旧前端"禁用"语义：DISABLED 归一化为 REVOKED
        if status == "DISABLED":
            status = "REVOKED"
        if status not in ("ACTIVE", "REVOKED", "EXPIRED"):
            raise ValueError("非法状态：仅支持 ACTIVE/REVOKED/EXPIRED")
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowApiKey, api_key_id)
            if row is None:
                return False
            row.status = status
            await session.commit()
            await _sync_redis_config(row.api_key, _row_config(row))
            return True

    @staticmethod
    async def delete(api_key_id: int) -> bool:
        async with mysql_client.get_session() as session:
            row = await session.get(WorkflowApiKey, api_key_id)
            if row is None:
                return False
            api_key = row.api_key
            await session.delete(row)
            await session.commit()
            try:
                await client.hdel(PREFIX_WORKFLOW_API_KEY, api_key)
            except Exception as e:  # noqa: BLE001
                log.error("delete workflow api key redis failed: {}", e)
            return True

    @staticmethod
    async def verify(api_key: str) -> Optional[dict]:
        """供 API 触发链路校验：有效返回 {workflowId, rateLimit}，并刷新使用统计。"""
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                select(WorkflowApiKey).where(WorkflowApiKey.api_key == api_key))).scalar_one_or_none()
            if row is None or row.status != "ACTIVE":
                return None
            if row.expire_time and row.expire_time < datetime.now():
                row.status = "EXPIRED"
                await session.commit()
                return None
            row.last_used_time = datetime.now()
            row.total_calls += 1
            await session.commit()
            return {"workflowId": row.workflow_id, "rateLimit": row.rate_limit}


async def verify_api_key(api_key: str) -> Optional[dict]:
    """独立函数入口（网关/外部调用方便引用）。"""
    try:
        return await WorkflowApiKeyService.verify(api_key)
    except Exception as e:  # noqa: BLE001
        log.error("verify_api_key failed: {}", e)
        return None
