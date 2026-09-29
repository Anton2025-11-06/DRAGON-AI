# -*- coding: utf-8 -*-
"""工作流定义服务：CRUD / 发布 / 版本 / 回滚 / 复制 / 模板 / 下拉数据源。

设计（参照 MaxKB Application/ApplicationVersion 的草稿-快照模型）：
- tb_workflow.graph 始终是草稿区；
- publish：校验 ERROR 清零 → 版本号+1 → 快照写 tb_workflow_version → 主表 current_version 更新；
- 执行（非 DEBUG）：只读 current_version 对应快照，与草稿互不干扰；
- 回滚：目标版本快照复制为草稿，不自动发布（发布由用户手动触发；历史不可变）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, or_, select, update

from common.common_constants.constant import PREFIX_WORKFLOW_API_KEY
from common.common_constants.model_constant import MODEL_TYPE_TEXT
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_COPY, ACTION_DELETE, ACTION_EDIT, ACTION_EXPORT, ACTION_TEMPLATE,
    ACTION_USE, ACTION_VIEW, build_visible_cond,
    decorate_actions, ensure_action,
)
from common.common_redis.redis import client
from common.common_utils.creator_util import attach_creator
from service.service_workflow.models.workflow_entity import (
    Workflow, WorkflowApiKey, WorkflowTemplate, WorkflowVersion,
)
from service.service_workflow.workflow_engine.graph import WorkflowGraph, WorkflowGraphError
from service.service_workflow.workflow_engine.node_definitions import get_node_definitions
from service.service_workflow.workflow_engine.templates import BUILTIN_TEMPLATES


def _now() -> datetime:
    return datetime.now()


class WorkflowService:
    """工作流定义服务（async，Session 由 mysql_client 单例提供）。"""

    # ==================== 分页 / 详情 ====================

    @staticmethod
    async def page(login_user: dict, current: int = 1, size: int = 10, name: str = None,
                   status: str = None, created_by: int = None):
        """列表：可见范围下推 SQL，行内 actions 由 ACL 求值下发（前端按钮的唯一来源）。"""
        async with mysql_client.get_session() as session:
            stmt = select(Workflow)
            if name:
                stmt = stmt.where(Workflow.name.like(f"%{name}%"))
            if status:
                stmt = stmt.where(Workflow.status == status)
            if created_by is not None:
                stmt = stmt.where(Workflow.created_by == created_by)
            # 可见 = 我创建的 ∪ 数据权限命中 ∪ 持有 view 授权（ADMIN 不限），与单点判定同源
            visible = build_visible_cond(login_user, "workflow", Workflow.id,
                                         Workflow.created_by)
            if visible is not None:
                stmt = stmt.where(visible)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await session.execute(
                stmt.order_by(Workflow.id.desc())
                .offset((current - 1) * size).limit(size))).scalars().all()
            records = [{
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "currentVersion": r.current_version,
                "status": r.status,
                "nodeCount": len((r.graph or {}).get("nodes") or []),
                # 行上带归属人：decorate_actions 据此判 owner/scope 档，前端也能显示创建人
                "created_by": r.created_by,
                "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
                "updateTime": r.update_time.strftime("%Y-%m-%d %H:%M:%S") if r.update_time else None,
            } for r in rows]
            await decorate_actions(login_user, "workflow", records, session=session)
            # 创建人展示名：别人看到按钮置灰时得知道找谁要授权（一次批量查，不按行查库）
            await attach_creator(records, session=session)
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    async def detail(login_user: dict, workflow_id: int) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return None
            # 列表里看得见不等于能打开详情：view 在这里再卡一次（防直推 id 探测）
            await ensure_action(login_user, "workflow", workflow_id, ACTION_VIEW,
                                owner_id=r.created_by, session=session)
            return {
                "id": str(r.id), "name": r.name, "description": r.description,
                "graph": r.graph, "inputVariables": r.input_variables,
                "outputVariables": r.output_variables,
                "currentVersion": r.current_version, "status": r.status,
                "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
                "updateTime": r.update_time.strftime("%Y-%m-%d %H:%M:%S") if r.update_time else None,
            }

    @staticmethod
    async def list_executable(login_user: dict) -> list[dict]:
        """可被【工作流】节点调用的工作流：已发布 + 至少有一把 ACTIVE 未过期 API Key。

        口径与节点执行器的运行前校验一致（没可用 key 就调不动），否则用户会在下拉里
        选到一个必然失败的子工作流。

        下拉按 use 而不是 view 过滤：能引用它跑起来不等于能看它的配置（scope_actions
        里两档恰好都在，但显式只授 view 的连接不会出现在这里）。

        顺手把可用 key 一起带出去：前端再拉一次 /workflow-api-keys/workflows/{id} 要多
        一个 workflow:apikey:list 权限，编辑器使用者不一定有，拿不到就只能面对一个
        「选不到 key」的死下拉。
        """
        now = _now()
        async with mysql_client.get_session() as session:
            stmt = select(Workflow).where(Workflow.status == "PUBLISHED",
                                          Workflow.current_version > 0)
            visible = build_visible_cond(login_user, "workflow", Workflow.id,
                                         Workflow.created_by, action=ACTION_USE)
            if visible is not None:
                stmt = stmt.where(visible)
            wfs = (await session.execute(
                stmt.order_by(Workflow.id.desc()))).scalars().all()
            if not wfs:
                return []
            keys = (await session.execute(
                select(WorkflowApiKey).where(
                    WorkflowApiKey.workflow_id.in_([w.id for w in wfs]),
                    WorkflowApiKey.status == "ACTIVE",
                    or_(WorkflowApiKey.expire_time.is_(None),
                        WorkflowApiKey.expire_time > now))
                .order_by(WorkflowApiKey.id.desc()))).scalars().all()
        grouped: dict[int, list] = {}
        for k in keys:
            grouped.setdefault(k.workflow_id, []).append({
                "id": k.id, "name": k.name, "rateLimit": k.rate_limit,
                "expireTime": k.expire_time.strftime("%Y-%m-%d %H:%M:%S") if k.expire_time else None,
            })
        return [{
            "id": str(w.id), "name": w.name, "description": w.description,
            "currentVersion": w.current_version, "apiKeyCount": len(grouped.get(w.id) or []),
            "apiKeys": grouped.get(w.id) or [],
        } for w in wfs if grouped.get(w.id)]

    # ==================== CRUD ====================

    @staticmethod
    async def create(req, user_id: int = 0) -> int:
        graph = req.graph
        if graph:
            await WorkflowService._validate_graph_soft(graph)
        async with mysql_client.get_session() as session:
            wf = Workflow(name=req.name, description=req.description, graph=graph,
                          input_variables=[v.model_dump() for v in req.inputVariables or []],
                          output_variables=[v.model_dump() for v in req.outputVariables or []],
                          status="DRAFT", created_by=user_id)
            session.add(wf)
            await session.commit()
            return wf.id

    @staticmethod
    async def update(login_user: dict, workflow_id: int, req) -> bool:
        graph = req.graph
        if graph:
            await WorkflowService._validate_graph_soft(graph)
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return False
            await ensure_action(login_user, "workflow", workflow_id, ACTION_EDIT,
                                owner_id=r.created_by, session=session)
            r.name = req.name
            r.description = req.description
            if graph is not None:
                r.graph = graph
            if req.inputVariables is not None:
                r.input_variables = [v.model_dump() for v in req.inputVariables]
            if req.outputVariables is not None:
                r.output_variables = [v.model_dump() for v in req.outputVariables]
            await session.commit()
            return True

    @staticmethod
    async def delete(login_user: dict, workflow_id: int) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return False
            await ensure_action(login_user, "workflow", workflow_id, ACTION_DELETE,
                                owner_id=r.created_by, session=session)
            # 级联删除前先取出 API Key（供 Redis 网关配置同步清理）
            api_keys = (await session.execute(
                select(WorkflowApiKey.api_key)
                .where(WorkflowApiKey.workflow_id == workflow_id))).scalars().all()
            await session.execute(delete(WorkflowVersion)
                                  .where(WorkflowVersion.workflow_id == workflow_id))
            await session.execute(delete(WorkflowApiKey)
                                  .where(WorkflowApiKey.workflow_id == workflow_id))
            await session.delete(r)
            await session.commit()
        # 删除后同步清理 Redis 中该工作流的 API Key 网关鉴权配置
        try:
            if api_keys:
                await client.hdel(PREFIX_WORKFLOW_API_KEY, *api_keys)
        except Exception as e:  # noqa: BLE001
            log.error("delete workflow api keys redis failed: {}", e)
        return True

    # ==================== 发布 / 复制 / 回滚 ====================

    @staticmethod
    async def publish(login_user: dict, workflow_id: int, change_log: str = None) -> dict:
        """发布：严格校验 → 快照 → 版本+1。返回 {version, issues}。"""
        user_id = int(login_user.get("user_id") or 0)
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            # 发布不单列动作：页面上发布就在编辑器的保存区，能改配置就能发布
            await ensure_action(login_user, "workflow", workflow_id, ACTION_EDIT,
                                owner_id=r.created_by, session=session)
            if not r.graph:
                raise ValueError("工作流图为空，无法发布")
            issues = WorkflowGraph(r.graph).validate(
                await WorkflowService._model_categories(r.graph, session))
            errors = [i for i in issues if i.severity == "ERROR"]
            if errors:
                raise WorkflowGraphError(errors)
            new_version = r.current_version + 1
            snap = WorkflowVersion(
                workflow_id=workflow_id, version=new_version,
                graph_snapshot=r.graph, input_variables=r.input_variables,
                output_variables=r.output_variables,
                change_log=change_log or r.description, published=1, created_by=user_id)
            session.add(snap)
            r.current_version = new_version
            r.status = "PUBLISHED"
            await session.commit()
            return {"version": new_version,
                    "issues": [i.to_dict() for i in issues if i.severity != "ERROR"]}

    @staticmethod
    async def copy(login_user: dict, workflow_id: int, name: str) -> int:
        """复制 = 读别人的配置 + 建自己的新实例，卡页面「复制」按钮；副本归属人为自己。"""
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            await ensure_action(login_user, "workflow", workflow_id, ACTION_COPY,
                                owner_id=r.created_by, session=session)
            new_wf = Workflow(
                name=name or f"{r.name} 副本", description=r.description,
                graph=r.graph, input_variables=r.input_variables,
                output_variables=r.output_variables, status="DRAFT",
                created_by=int(login_user.get("user_id") or 0))
            session.add(new_wf)
            await session.commit()
            return new_wf.id

    # ==================== 版本 ====================

    @staticmethod
    async def versions(login_user: dict, workflow_id: int) -> list[dict]:
        """版本历史也是配置的一部分：没有 view 就不给看（旧版本的图能拼回原工作流）。"""
        async with mysql_client.get_session() as session:
            owner = await session.scalar(
                select(Workflow.created_by).where(Workflow.id == workflow_id))
            if owner is None:
                raise ValueError("工作流不存在")
            await ensure_action(login_user, "workflow", workflow_id, ACTION_VIEW,
                                owner_id=owner, session=session)
            rows = (await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id)
                .order_by(WorkflowVersion.version.desc()))).scalars().all()
            return [{
                "id": str(r.id), "workflowId": str(r.workflow_id), "version": r.version,
                "graphSnapshot": r.graph_snapshot, "changeLog": r.change_log,
                "published": bool(r.published), "createdBy": str(r.created_by),
                "createdTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
            } for r in rows]

    @staticmethod
    async def version_detail(login_user: dict, workflow_id: int, version: int) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            owner = await session.scalar(
                select(Workflow.created_by).where(Workflow.id == workflow_id))
            if owner is None:
                return None
            await ensure_action(login_user, "workflow", workflow_id, ACTION_VIEW,
                                owner_id=owner, session=session)
            r = (await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id,
                                              WorkflowVersion.version == version))).scalar_one_or_none()
            if r is None:
                return None
            return {
                "id": str(r.id), "workflowId": str(r.workflow_id), "version": r.version,
                "graphSnapshot": r.graph_snapshot, "changeLog": r.change_log,
                "published": bool(r.published), "createdBy": str(r.created_by),
                "createdTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
            }

    @staticmethod
    async def rollback(login_user: dict, workflow_id: int, version: int) -> int:
        """回滚：目标版本快照覆盖草稿，不自动发布（发布由用户手动触发）。"""
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            v = (await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id,
                                              WorkflowVersion.version == version))).scalar_one_or_none()
            if r is None or v is None:
                raise ValueError("工作流或版本不存在")
            # 回滚改的是草稿，性质上等于一次 edit
            await ensure_action(login_user, "workflow", workflow_id, ACTION_EDIT,
                                owner_id=r.created_by, session=session)
            r.graph = v.graph_snapshot
            r.input_variables = v.input_variables
            r.output_variables = v.output_variables
            await session.commit()
        # 仅覆盖草稿，不自动发布：是否对外生效由用户在编辑器手动点「发布」决定
        return version

    @staticmethod
    async def get_snapshot(workflow_id: int, version: Optional[int] = None) -> Optional[dict]:
        """取发布快照（执行用）。version=None → 当前版本。"""
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return None
            ver = version if version is not None else r.current_version
            if ver == 0:
                return None  # 从未发布
            v = (await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id,
                                              WorkflowVersion.version == ver))).scalar_one_or_none()
            return v.graph_snapshot if v else None

    # ==================== 校验 ====================

    @staticmethod
    async def _model_categories(graph: dict, session) -> dict:
        """取图内开记忆节点所用模型的能力类型 {str(model_id): category}。

        只为记忆校验服务（哪类模型能开记忆、压缩模型是不是文生文），没开记忆的
        工作流一次库也不查。其他校验填不上这个参数（拿 None 走降级分支）。
        """
        ids: set[int] = set()
        for n in (graph or {}).get("nodes") or []:
            data = n.get("data") or {}
            if n.get("type") != "LLM" or data.get("memoryEnabled") is not True:
                continue
            for key in ("modelId", "memoryCompressModelId"):
                mid = data.get(key)
                if mid:
                    ids.add(int(mid))
        if not ids:
            return {}
        from service.service_system.models.model import Model
        rows = (await session.execute(
            select(Model.id, Model.category).where(Model.id.in_(ids)))).all()
        return {str(r[0]): r[1] for r in rows}

    @staticmethod
    async def _validate_graph_soft(graph: dict) -> list[dict]:
        """保存草稿时的宽松校验（ERROR 只提示不阻断保存）。"""
        try:
            async with mysql_client.get_session() as session:
                cats = await WorkflowService._model_categories(graph, session)
            issues = WorkflowGraph(graph).validate(cats)
        except Exception as e:  # noqa: BLE001
            log.warning("graph 解析失败: {}", e)
            return []
        return [i.to_dict() for i in issues]

    @staticmethod
    async def validate_graph(login_user: dict, workflow_id: int) -> dict:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            await ensure_action(login_user, "workflow", workflow_id, ACTION_VIEW,
                                owner_id=r.created_by, session=session)
            issues = (WorkflowGraph(r.graph or {}).validate(
                await WorkflowService._model_categories(r.graph or {}, session))
                if r.graph else [])
            return {"valid": not any(i.severity == "ERROR" for i in issues),
                    "issues": [i.to_dict() for i in issues]}

    # ==================== 节点定义 / 下拉数据源 ====================

    @staticmethod
    def node_definitions() -> list[dict]:
        return get_node_definitions()

    @staticmethod
    async def list_models(model_type: str = MODEL_TYPE_TEXT, user_id: int = 0) -> list[dict]:
        """模型下拉（对接模型广场 tb_model，仅返回当前用户**审批通过**的指定类型模型）。

        type 取值见 common.common_constants.model_constant 的 MODEL_TYPE_* 常量；
        user_id=0（未登录/旁路调用）视为无权，返回空列表。
        """
        from common.common_constants.model_constant import (
            MODEL_TYPE_CATEGORY_MAP,
            MODEL_TYPE_FALLBACK_CATEGORIES,
        )
        categories = MODEL_TYPE_CATEGORY_MAP.get(model_type, MODEL_TYPE_FALLBACK_CATEGORIES)
        if user_id <= 0:
            return []
        from service.service_system.models.model import Model, ModelApply
        stmt = (
            select(
                Model.id, Model.provider, Model.category, Model.name, Model.model_name,
            )
            .join(ModelApply, ModelApply.model_id == Model.id)
            .where(
                Model.status == 1,                    # 模型启用
                Model.category.in_(categories),       # 指定类型
                ModelApply.user_id == user_id,        # 当前用户
                ModelApply.status == 1,               # 审批已通过
            )
            .distinct()                               # 同一模型多次申请/通过只出现一次
        )
        async with mysql_client.get_session() as session:
            rows = (await session.execute(stmt)).mappings().all()
            return [{
                "id": r["id"], "provider": r["provider"], "type": r["category"],
                "name": r["name"], "modelName": r["model_name"],
                # 不带 baseUrl：节点配置只需要选模型，调用统一走网关 + 用户自己的授权 key，
                # 厂商基址属于管理端凭据
            } for r in rows]

    @staticmethod
    async def get_name(workflow_id: int) -> Optional[str]:
        """引擎侧取工作流名（不带权限判定：执行链路里没有登录用户）。"""
        async with mysql_client.get_session() as session:
            return await session.scalar(
                select(Workflow.name).where(Workflow.id == workflow_id))

    @staticmethod
    async def get_run_info(workflow_id: int, version: Optional[int] = None) -> Optional[dict]:
        """引擎侧取「能不能跑」的那几个事实：名称 / 当前版本 / 目标版本是否已发布。

        子工作流节点与 LLM 的内嵌子流工具靠 api-key 决定能不能调（机器通道不进 ACL），
        所以这里不做判定、也只给跑起来所需的最小信息，不把别人的图定义顺带带出去。
        """
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return None
            ver = int(version or 0)
            published = r.current_version > 0
            if ver:
                published = bool(await session.scalar(
                    select(WorkflowVersion.published).where(
                        WorkflowVersion.workflow_id == workflow_id,
                        WorkflowVersion.version == ver)) or 0)
            return {"name": r.name, "currentVersion": r.current_version, "published": published}

    @staticmethod
    async def list_knowledge_bases(login_user: dict) -> list[dict]:
        """知识库下拉（service_rag.tb_knowledge_base）：按 use 过滤。

        这张表的 ORM 在另一个服务里，用 table() 声明最小列集拼查询，
        条件仍由 resource_guard 生成——不在这里手写一份 ACL SQL。
        """
        from sqlalchemy import column, table as sql_table
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
        except Exception as e:  # noqa: BLE001  表未初始化时降级为空
            log.warning("list_knowledge_bases failed: {}", e)
            return []

    @staticmethod
    async def list_chat_agents(user_id: int) -> list[dict]:
        """智能体下拉（模型对话会话 tb_model_chat_session）。"""
        from sqlalchemy import text as sql_text
        try:
            async with mysql_client.get_session() as session:
                rows = (await session.execute(sql_text(
                    "SELECT id, title FROM tb_model_chat_session "
                    "WHERE user_id=:u ORDER BY id DESC LIMIT 100"),
                    {"u": user_id})).mappings().all()
                return [{"id": r["id"], "name": r["title"] or f"会话{r['id']}"} for r in rows]
        except Exception as e:  # noqa: BLE001
            log.warning("list_chat_agents failed: {}", e)
            return []

    # ==================== 模板 ====================

    @staticmethod
    async def seed_builtin_templates() -> int:
        """启动时幂等写入内置模板。"""
        async with mysql_client.get_session() as session:
            count = await session.scalar(
                select(func.count()).select_from(WorkflowTemplate)
                .where(WorkflowTemplate.is_built_in == 1))
            if count and int(count) >= len(BUILTIN_TEMPLATES):
                return 0
            for t in BUILTIN_TEMPLATES:
                exists = (await session.execute(
                    select(WorkflowTemplate).where(WorkflowTemplate.is_built_in == 1,
                                                   WorkflowTemplate.name == t["name"])
                )).scalar_one_or_none()
                if exists:
                    continue
                session.add(WorkflowTemplate(
                    name=t["name"], description=t["description"], category=t["category"],
                    icon=t.get("icon"), graph=t["graph"], is_built_in=1))
            await session.commit()
            return len(BUILTIN_TEMPLATES)

    @staticmethod
    async def template_page(login_user: dict, current: int = 1, size: int = 10, name: str = None,
                            category: str = None, built_in_only: bool = False):
        async with mysql_client.get_session() as session:
            stmt = select(WorkflowTemplate)
            if name:
                stmt = stmt.where(WorkflowTemplate.name.like(f"%{name}%"))
            if category:
                stmt = stmt.where(WorkflowTemplate.category == category)
            if built_in_only:
                stmt = stmt.where(WorkflowTemplate.is_built_in == 1)
            visible = build_visible_cond(login_user, "workflow_template", WorkflowTemplate.id,
                                         WorkflowTemplate.created_by)
            if visible is not None:
                stmt = stmt.where(visible)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await session.execute(
                stmt.order_by(WorkflowTemplate.id.desc())
                .offset((current - 1) * size).limit(size))).scalars().all()
            records = [WorkflowService._template_dto(r) for r in rows]
            await decorate_actions(login_user, "workflow_template", records, session=session)
            await attach_creator(records, session=session)
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    def _template_dto(r: WorkflowTemplate) -> dict:
        return {
            "id": str(r.id), "name": r.name, "description": r.description,
            "category": r.category, "icon": r.icon, "graph": r.graph,
            "builtIn": bool(r.is_built_in),
            "created_by": r.created_by,
            "nodeCount": len((r.graph or {}).get("nodes") or []),
            "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
        }

    @staticmethod
    async def template_detail(login_user: dict, template_id: int,
                              action: str = ACTION_VIEW) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                return None
            # 同一取数接口服务多个按钮（看详情 / 导出 JSON），卡哪个动作由调用方给
            await ensure_action(login_user, "workflow_template", template_id, action,
                                owner_id=r.created_by, session=session)
            return WorkflowService._template_dto(r)

    @staticmethod
    async def create_template(req, user_id: int = 0) -> int:
        async with mysql_client.get_session() as session:
            t = WorkflowTemplate(name=req.name, description=req.description,
                                 category=req.category, icon=req.icon, graph=req.graph or {},
                                 input_variables=[v.model_dump() for v in req.inputVariables or []],
                                 output_variables=[v.model_dump() for v in req.outputVariables or []],
                                 is_built_in=0, created_by=user_id)
            session.add(t)
            await session.commit()
            return t.id

    @staticmethod
    async def copy_template(login_user: dict, template_id: int,
                            name: str = None) -> int:
        """复制为自定义模板（模板页「复制」按钮）：读源模板图 + 建自己的新实例，副本归属人为自己。

        不让前端拿列表里的 graph 直接 create：那样 copy 这个动作只在按钮上生效，
        绕过页面推接口就能白拿别人的模板。
        """
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                raise ValueError("模板不存在")
            await ensure_action(login_user, "workflow_template", template_id, ACTION_COPY,
                                owner_id=r.created_by, session=session)
            t = WorkflowTemplate(name=(name or f"{r.name} - 副本")[:128],
                                 description=r.description, category=r.category,
                                 icon=r.icon, graph=r.graph,
                                 input_variables=r.input_variables,
                                 output_variables=r.output_variables,
                                 is_built_in=0,
                                 created_by=int(login_user.get("user_id") or 0))
            session.add(t)
            await session.commit()
            return t.id

    @staticmethod
    async def update_template(login_user: dict, template_id: int, req) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                return False
            if r.is_built_in:
                raise ValueError("内置模板不可修改")
            await ensure_action(login_user, "workflow_template", template_id, ACTION_EDIT,
                                owner_id=r.created_by, session=session)
            r.name = req.name
            r.description = req.description
            r.category = req.category
            r.icon = req.icon
            if req.graph is not None:
                r.graph = req.graph
            await session.commit()
            return True

    @staticmethod
    async def delete_template(login_user: dict, template_id: int) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                return False
            if r.is_built_in:
                raise ValueError("内置模板不可删除")
            await ensure_action(login_user, "workflow_template", template_id, ACTION_DELETE,
                                owner_id=r.created_by, session=session)
            await session.delete(r)
            await session.commit()
            return True

    @staticmethod
    async def template_from_workflow(login_user: dict, workflow_id: int, name: str,
                                     category: str, description: str = None) -> int:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            # 把工作流抽成模板 = 页面「保存为模块」按钮；新模板归属人是自己
            await ensure_action(login_user, "workflow", workflow_id, ACTION_TEMPLATE,
                                owner_id=r.created_by, session=session)
            t = WorkflowTemplate(name=name, description=description,
                                 category=category or "CUSTOM", graph=r.graph or {},
                                 input_variables=r.input_variables,
                                 output_variables=r.output_variables,
                                 is_built_in=0,
                                 created_by=int(login_user.get("user_id") or 0))
            session.add(t)
            await session.commit()
            return t.id

    @staticmethod
    async def create_from_template(login_user: dict, template_id: int, name: str,
                                   description: str = None) -> int:
        async with mysql_client.get_session() as session:
            t = await session.get(WorkflowTemplate, template_id)
            if t is None:
                raise ValueError("模板不存在")
            # 从模板建工作流 = 使用模板（内置模板人人可用，别人的自定义模板需要 use 授权）
            await ensure_action(login_user, "workflow_template", template_id, ACTION_USE,
                                owner_id=t.created_by, session=session)
            wf = Workflow(name=name or t.name, description=description or t.description,
                          graph=t.graph, input_variables=t.input_variables,
                          output_variables=t.output_variables, status="DRAFT",
                          created_by=int(login_user.get("user_id") or 0))
            session.add(wf)
            await session.commit()
            return wf.id

    @staticmethod
    async def templates_by_category(login_user: dict, category: str) -> list[dict]:
        """按分类的模板列表（与 template_page 同口径，只是不分页）。"""
        async with mysql_client.get_session() as session:
            stmt = select(WorkflowTemplate).where(WorkflowTemplate.category == category)
            visible = build_visible_cond(login_user, "workflow_template", WorkflowTemplate.id,
                                         WorkflowTemplate.created_by)
            if visible is not None:
                stmt = stmt.where(visible)
            rows = (await session.execute(
                stmt.order_by(WorkflowTemplate.id.desc()))).scalars().all()
            return [WorkflowService._template_dto(r) for r in rows]

    @staticmethod
    async def builtin_templates() -> list[dict]:
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.is_built_in == 1)
                .order_by(WorkflowTemplate.id))).scalars().all()
            return [WorkflowService._template_dto(r) for r in rows]

    @staticmethod
    def export_template_dto(r: WorkflowTemplate) -> str:
        """导出为 JSON 字符串（前端 download 用）。"""
        payload = {"name": r.name, "description": r.description, "category": r.category,
                   "icon": r.icon, "graph": r.graph}
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    async def import_template(json_str: str, user_id: int = 0) -> int:
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"模板 JSON 解析失败: {e}") from e
        if not isinstance(data, dict) or not data.get("name") or not data.get("graph"):
            raise ValueError("模板 JSON 缺少 name/graph 字段")
        async with mysql_client.get_session() as session:
            t = WorkflowTemplate(name=str(data["name"])[:128],
                                 description=data.get("description"),
                                 category=data.get("category") or "CUSTOM",
                                 icon=data.get("icon"), graph=data["graph"],
                                 is_built_in=0, created_by=user_id)
            session.add(t)
            await session.commit()
            return t.id


# ==================== 模块初始化（幂等，懒加载） ====================

import asyncio as _asyncio

_initialized = False
_init_lock = None


async def ensure_initialized() -> None:
    """幂等初始化（FastAPI 依赖，挂在工作流相关路由上，每次请求先跑一次）。

    职责：
    - 内置模板种子（tb_workflow_template，首次启动写入，已存在则跳过）
    - 注入默认模型 Provider（WorkflowModelClient 走 tb_model + Redis 缓存）

    设计为懒加载而非 lifespan 钩子，原因：create_app 的 lifespan 在 mysql/redis
    初始化后才 yield，但本模块无法篡改其生命周期；用请求级依赖可保证「DB 就绪后」
    才执行，且失败可自愈（不置位 _initialized，下次请求重试）。
    """
    global _initialized, _init_lock
    if _initialized:
        return
    if _init_lock is None:
        _init_lock = _asyncio.Lock()
    async with _init_lock:
        if _initialized:
            return
        try:
            await WorkflowService.seed_builtin_templates()
            from service.service_workflow.services.workflow_execution_service import (
                get_model_provider,
            )
            from service.service_workflow.workflow_engine.model_client import (
                WorkflowModelClient,
            )
            WorkflowModelClient.set_default_provider(get_model_provider())
            _initialized = True
        except Exception as e:  # noqa: BLE001  DB 未就绪等：自愈，下次请求重试
            log.warning("workflow ensure_initialized skipped: {}", e)
