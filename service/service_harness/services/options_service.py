# -*- coding: utf-8 -*-
"""深度探索四类能力挂载候选（use 口径：自己创建 ∪ ACL 授权可用）。

数据来源与各管理页下拉同源，不另造一套 ACL SQL：
- kb：tb_knowledge_base 轻量查——这张表的 ORM 在 service_rag，本服务不 import
  它，跨表用 sql_table() 声明最小列集拼查询，可见条件仍由 resource_guard 生成
  （workflow 服务 list_knowledge_bases 同款写法）；
- mcp：McpServerService.options（进程内 import，use 口径，仅启用中）；
- tool：ToolService.options（进程内 import，use 口径，仅启用中，带参数定义）；
- skill：tb_skill 轻量查（use 口径 + 启用中，ORM 在 service_workflow；code
  一并带出——前端展示与 worker 侧定位 zip 都用得上）。

use 口径的语义：下拉选得中的即 worker 侧可用（选不中等于用不了，比「选得到
但挂载报无权」友好）。执行侧 ACL（挂载校验、kb_search 白名单）在 M2-2 的
worker 桥里 ensure_action 再卡一道，防直推 id 绕过前端。
"""
from __future__ import annotations

from sqlalchemy import column, select, table as sql_table

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import ACTION_USE, build_visible_cond

# 挂载候选类型（路由 /options/{kind} 的合法取值，顺序即前端 picker 顺序）
OPTIONS_KINDS = ("kb", "mcp", "tool", "skill")


class HarnessOptionsService:
    """四类能力挂载候选清单（前端 picker 数据源，统一平铺结构）。"""

    @staticmethod
    async def options(login_user: dict, kind: str) -> list[dict]:
        if kind == "kb":
            return await HarnessOptionsService._kb_options(login_user)
        if kind == "mcp":
            return await HarnessOptionsService._mcp_options(login_user)
        if kind == "tool":
            return await HarnessOptionsService._tool_options(login_user)
        if kind == "skill":
            return await HarnessOptionsService._skill_options(login_user)
        raise ValueError(f"未知能力类型: {kind}")

    @staticmethod
    async def _kb_options(login_user: dict) -> list[dict]:
        kb = sql_table("tb_knowledge_base", column("kb_id"), column("kb_name"),
                       column("description"), column("created_by"), column("is_deleted"))
        try:
            async with mysql_client.get_session() as session:
                stmt = select(kb.c.kb_id, kb.c.kb_name, kb.c.description).where(
                    kb.c.is_deleted == 0)
                visible = build_visible_cond(login_user, "knowledge_base", kb.c.kb_id,
                                             kb.c.created_by, action=ACTION_USE)
                if visible is not None:
                    stmt = stmt.where(visible)
                rows = (await session.execute(
                    stmt.order_by(kb.c.kb_id.desc()))).mappings().all()
                return [{"id": r["kb_id"], "name": r["kb_name"],
                         "description": r["description"]} for r in rows]
        except Exception as e:  # noqa: BLE001  rag 表未初始化时降级为空，picker 不 500
            log.warning("harness kb options failed: {}", e)
            return []

    @staticmethod
    async def _mcp_options(login_user: dict) -> list[dict]:
        """MCP 连接候选：进程内 import 调 McpServerService.options（use 口径）。

        返回自带 type 字段（SSE/STDIO），前端 picker 可展示连接类型，原样透传。
        """
        try:
            from service.service_workflow.services.mcp_service import McpServerService
            return list(await McpServerService.options(login_user))
        except Exception as e:  # noqa: BLE001  mcp SDK 未装等场景降级为空
            log.warning("harness mcp options failed: {}", e)
            return []

    @staticmethod
    async def _tool_options(login_user: dict) -> list[dict]:
        """平台工具候选：ToolService.options（use 口径，含参数定义）。

        parameters/timeout 是给 worker 侧工具桥生成入参 schema 用的，picker
        不需要，裁成 id/name/description 三键平铺保持四口结构一致。
        """
        try:
            from service.service_workflow.services.tool_service import ToolService
            rows = await ToolService.options(login_user)
            return [{"id": r["id"], "name": r["name"],
                     "description": r["description"]} for r in rows]
        except Exception as e:  # noqa: BLE001
            log.warning("harness tool options failed: {}", e)
            return []

    @staticmethod
    async def _skill_options(login_user: dict) -> list[dict]:
        skill = sql_table("tb_skill", column("id"), column("name"), column("code"),
                          column("description"), column("status"), column("created_by"))
        try:
            async with mysql_client.get_session() as session:
                stmt = select(skill.c.id, skill.c.name, skill.c.code,
                              skill.c.description).where(skill.c.status == 1)
                visible = build_visible_cond(login_user, "skill", skill.c.id,
                                             skill.c.created_by, action=ACTION_USE)
                if visible is not None:
                    stmt = stmt.where(visible)
                rows = (await session.execute(
                    stmt.order_by(skill.c.id.desc()))).mappings().all()
                return [{"id": r["id"], "name": r["name"], "code": r["code"],
                         "description": r["description"]} for r in rows]
        except Exception as e:  # noqa: BLE001
            log.warning("harness skill options failed: {}", e)
            return []
