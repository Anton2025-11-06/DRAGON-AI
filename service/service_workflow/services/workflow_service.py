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

from sqlalchemy import delete, func, select, update

from common.common_constants.constant import PREFIX_WORKFLOW_API_KEY
from common.common_constants.model_constant import MODEL_TYPE_TEXT
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client
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
    async def page(current: int = 1, size: int = 10, name: str = None,
                   status: str = None, created_by: int = None):
        async with mysql_client.get_session() as session:
            stmt = select(Workflow)
            if name:
                stmt = stmt.where(Workflow.name.like(f"%{name}%"))
            if status:
                stmt = stmt.where(Workflow.status == status)
            if created_by is not None:
                stmt = stmt.where(Workflow.created_by == created_by)
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
                "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
                "updateTime": r.update_time.strftime("%Y-%m-%d %H:%M:%S") if r.update_time else None,
            } for r in rows]
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    async def detail(workflow_id: int) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return None
            return {
                "id": str(r.id), "name": r.name, "description": r.description,
                "graph": r.graph, "inputVariables": r.input_variables,
                "outputVariables": r.output_variables,
                "currentVersion": r.current_version, "status": r.status,
                "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
                "updateTime": r.update_time.strftime("%Y-%m-%d %H:%M:%S") if r.update_time else None,
            }

    # ==================== CRUD ====================

    @staticmethod
    async def create(req, user_id: int = 0) -> int:
        graph = req.graph
        if graph:
            WorkflowService._validate_graph_soft(graph)
        async with mysql_client.get_session() as session:
            wf = Workflow(name=req.name, description=req.description, graph=graph,
                          input_variables=[v.model_dump() for v in req.inputVariables or []],
                          output_variables=[v.model_dump() for v in req.outputVariables or []],
                          status="DRAFT", created_by=user_id)
            session.add(wf)
            await session.commit()
            return wf.id

    @staticmethod
    async def update(workflow_id: int, req, user_id: int = 0) -> bool:
        graph = req.graph
        if graph:
            WorkflowService._validate_graph_soft(graph)
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return False
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
    async def delete(workflow_id: int) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                return False
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
    async def publish(workflow_id: int, change_log: str = None, user_id: int = 0) -> dict:
        """发布：严格校验 → 快照 → 版本+1。返回 {version, issues}。"""
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            if not r.graph:
                raise ValueError("工作流图为空，无法发布")
            issues = WorkflowGraph(r.graph).validate()
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
    async def copy(workflow_id: int, name: str, user_id: int = 0) -> int:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            new_wf = Workflow(
                name=name or f"{r.name} 副本", description=r.description,
                graph=r.graph, input_variables=r.input_variables,
                output_variables=r.output_variables, status="DRAFT", created_by=user_id)
            session.add(new_wf)
            await session.commit()
            return new_wf.id

    # ==================== 版本 ====================

    @staticmethod
    async def versions(workflow_id: int) -> list[dict]:
        async with mysql_client.get_session() as session:
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
    async def version_detail(workflow_id: int, version: int) -> Optional[dict]:
        async with mysql_client.get_session() as session:
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
    async def rollback(workflow_id: int, version: int, user_id: int = 0) -> int:
        """回滚：目标版本快照覆盖草稿，不自动发布（发布由用户手动触发）。"""
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            v = (await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id,
                                              WorkflowVersion.version == version))).scalar_one_or_none()
            if r is None or v is None:
                raise ValueError("工作流或版本不存在")
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
    def _validate_graph_soft(graph: dict) -> list[dict]:
        """保存草稿时的宽松校验（ERROR 只提示不阻断保存）。"""
        try:
            issues = WorkflowGraph(graph).validate()
        except Exception as e:  # noqa: BLE001
            log.warning("graph 解析失败: {}", e)
            return []
        return [i.to_dict() for i in issues]

    @staticmethod
    async def validate_graph(workflow_id: int) -> dict:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            issues = WorkflowGraph(r.graph or {}).validate() if r.graph else []
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
                Model.id, Model.provider, Model.category, Model.name, Model.base_url,
                Model.is_direct, Model.suffixes,
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
                "name": r["name"], "baseUrl": r["base_url"],
                "isDirect": int(r["is_direct"]),
                "suffixes": r["suffixes"] or [],
            } for r in rows]

    @staticmethod
    async def list_knowledge_bases() -> list[dict]:
        """知识库下拉（service_rag.tb_knowledge_base）。"""
        from sqlalchemy import text as sql_text
        try:
            async with mysql_client.get_session() as session:
                rows = (await session.execute(sql_text(
                    "SELECT kb_id, kb_name, description FROM tb_knowledge_base "
                    "WHERE is_deleted=0 ORDER BY kb_id DESC"
                ))).mappings().all()
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
    async def template_page(current: int = 1, size: int = 10, name: str = None,
                            category: str = None, built_in_only: bool = False):
        async with mysql_client.get_session() as session:
            stmt = select(WorkflowTemplate)
            if name:
                stmt = stmt.where(WorkflowTemplate.name.like(f"%{name}%"))
            if category:
                stmt = stmt.where(WorkflowTemplate.category == category)
            if built_in_only:
                stmt = stmt.where(WorkflowTemplate.is_built_in == 1)
            total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await session.execute(
                stmt.order_by(WorkflowTemplate.id.desc())
                .offset((current - 1) * size).limit(size))).scalars().all()
            records = [WorkflowService._template_dto(r) for r in rows]
            return {"records": records, "total": total, "current": current, "size": size}

    @staticmethod
    def _template_dto(r: WorkflowTemplate) -> dict:
        return {
            "id": str(r.id), "name": r.name, "description": r.description,
            "category": r.category, "icon": r.icon, "graph": r.graph,
            "builtIn": bool(r.is_built_in),
            "nodeCount": len((r.graph or {}).get("nodes") or []),
            "createTime": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
        }

    @staticmethod
    async def template_detail(template_id: int) -> Optional[dict]:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            return WorkflowService._template_dto(r) if r else None

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
    async def update_template(template_id: int, req) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                return False
            if r.is_built_in:
                raise ValueError("内置模板不可修改")
            r.name = req.name
            r.description = req.description
            r.category = req.category
            r.icon = req.icon
            if req.graph is not None:
                r.graph = req.graph
            await session.commit()
            return True

    @staticmethod
    async def delete_template(template_id: int) -> bool:
        async with mysql_client.get_session() as session:
            r = await session.get(WorkflowTemplate, template_id)
            if r is None:
                return False
            if r.is_built_in:
                raise ValueError("内置模板不可删除")
            await session.delete(r)
            await session.commit()
            return True

    @staticmethod
    async def template_from_workflow(workflow_id: int, name: str, category: str,
                                     description: str = None, user_id: int = 0) -> int:
        async with mysql_client.get_session() as session:
            r = await session.get(Workflow, workflow_id)
            if r is None:
                raise ValueError("工作流不存在")
            t = WorkflowTemplate(name=name, description=description,
                                 category=category or "CUSTOM", graph=r.graph or {},
                                 input_variables=r.input_variables,
                                 output_variables=r.output_variables,
                                 is_built_in=0, created_by=user_id)
            session.add(t)
            await session.commit()
            return t.id

    @staticmethod
    async def create_from_template(template_id: int, name: str, description: str = None,
                                   user_id: int = 0) -> int:
        async with mysql_client.get_session() as session:
            t = await session.get(WorkflowTemplate, template_id)
            if t is None:
                raise ValueError("模板不存在")
            wf = Workflow(name=name or t.name, description=description or t.description,
                          graph=t.graph, input_variables=t.input_variables,
                          output_variables=t.output_variables, status="DRAFT",
                          created_by=user_id)
            session.add(wf)
            await session.commit()
            return wf.id

    @staticmethod
    async def templates_by_category(category: str) -> list[dict]:
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.category == category)
                .order_by(WorkflowTemplate.id.desc()))).scalars().all()
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
