# -*- coding: utf-8 -*-
"""知识库服务：三类知识库的分页 / 详情 / 下拉 / 新建 / 修改 / 停用 / 删除。

四条主线，改动前先对着看：

1. **权限两层各管一段**（SPEC §3.1/§3.2）：菜单可见与「能不能新建」走功能权限
   （ai:kb:list、ai:kb:add，挂在 router 的 @has_permission 上）；「能不能看/改/删这一条
   知识库、能不能往它里传文档」走资源 ACL（common_permission.resource_guard）——
   列表用 build_visible_cond 下推 SQL，行内按钮用 decorate_actions 下发，单点用
   ensure_action 判定。三处共用同一份主体展开，不会出现「列表里看不见但单点判定通过」。
2. **kb_type 与向量模型创建后锁定**：类型决定解析、分块与存储链路，向量模型决定向量空间
   （ES 的 dense_vector 维度建索引即固定 1024 维）。改这两个等于把库里历史切片全部作废，
   所以 modify 里显式拒绝——不是「前端不给改」，是后端根本不接这个值。
3. **三份 JSON 配置写时归一、读时也归一**：库里可能是 NULL（老行）或用户手写的残缺配置。
   落库前 normalize（只认白名单键，非法值回落默认），读出后再 normalize 一遍补齐缺键，
   下游（解析器 / 分块器 / 检索）拿到的永远是一份字段齐全的字典，不必各自 if key in cfg。
4. **version 只随「会让已有切片作废」的改动 +1**：解析引擎、预处理开关、分块配置变了才 +1，
   文档行的 parse_version 落后于它即代表需要重解析（列表页 needReparse）；
   改名称、描述、检索配置不动 version——检索配置对已落库的向量毫无影响，下次检索即时生效。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_constants import rag_constant as RC
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import (
    ACTION_DELETE, ACTION_EDIT, ACTION_USE, ACTION_VIEW, build_visible_cond,
    decorate_actions, ensure_action, maybe_session,
)
from common.common_utils.creator_util import attach_creator
from service.service_rag.models.kb_entity import Document, DocumentChunk, KnowledgeBase
from service.service_rag.schemas.rag_schema import KbPageReq, KbSaveReq, KnowledgeBaseResp
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.task_service import RagTaskService

# resource_guard 里的资源码（与 RESOURCE_SPECS / RESOURCE_TABLES 的键一致）
RESOURCE = "knowledge_base"
# dump() 出来的行是 camelCase，授权与创建人补全按这份键名取归属列
OWNER_KEY = "createdBy"


class KnowledgeBaseService:
    """知识库元数据与配置管理（不含文档、切片、检索，那些在各自的 service 里）"""

    # ==================== 内部：加载与配置读取 ====================
    @staticmethod
    async def load(session: AsyncSession, kb_id: int, *, alive: bool = True
                   ) -> Optional[KnowledgeBase]:
        """按 id 取知识库行；alive=False 时连软删行一起给（删除后的清理链路还要读它）。"""
        conds = [KnowledgeBase.kb_id == int(kb_id)]
        if alive:
            conds.append(KnowledgeBase.is_deleted == 0)
        return (await session.execute(
            select(KnowledgeBase).where(*conds))).scalar_one_or_none()

    @staticmethod
    async def require(login_user: dict, kb_id: int, action: str, *, session=None
                      ) -> KnowledgeBase:
        """取行 + 卡动作，是这一层唯一的鉴权入口（其余方法一律先经过它）。

        不存在只说「知识库不存在」（软删与脏 id 不必区分），无权限交给 ensure_action 抛 403
        并带上「联系作者申请授权」——把 403 说成 404 会让用户去翻回收站。

        返回的是绑定在会话上的 ORM 行，调用方传了自己的 session 才能在出块后继续读字段。
        """
        async with maybe_session(session) as s:
            kb = await KnowledgeBaseService.load(s, kb_id)
            if not kb:
                raise ValueError("知识库不存在")
            await ensure_action(login_user, RESOURCE, kb_id, action,
                                owner_id=kb.created_by, session=s)
            return kb

    @staticmethod
    async def visible_ids(login_user: dict, *, session=None, action: str = ACTION_USE,
                          only_enabled: bool = True) -> list[int]:
        """当前用户可见（且可按该动作使用）的知识库 id 列表。

        检索与图谱的前置白名单就来自这里（SPEC §10.2）：ES 与 Neo4j 是裸查存储，
        权限只在这一层收口，所以任何一次底层检索都必须带着这份 kb_id 集合下去。
        """
        async with maybe_session(session) as s:
            conds = [KnowledgeBase.is_deleted == 0]
            if only_enabled:
                conds.append(KnowledgeBase.status == 1)
            visible = build_visible_cond(login_user, RESOURCE,
                                         KnowledgeBase.kb_id, KnowledgeBase.created_by,
                                         action=action)
            if visible is not None:
                conds.append(visible)
            rows = (await s.execute(
                select(KnowledgeBase.kb_id).where(*conds))).scalars().all()
            return [int(i) for i in rows]

    @staticmethod
    def parse_of(kb: KnowledgeBase) -> tuple[str, dict]:
        """读库时再归一一次：老行的 parse_config 可能是 NULL 或缺键。"""
        return settings.normalize_parse_config(kb.kb_type, kb.parser_engine, kb.parse_config)

    @staticmethod
    def chunk_of(kb: KnowledgeBase) -> dict:
        return settings.normalize_chunk_config(kb.chunk_config)

    @staticmethod
    def retrieve_of(kb: KnowledgeBase) -> dict:
        return settings.normalize_retrieve_config(kb.retrieve_config)

    # ==================== 查询 ====================
    @staticmethod
    async def page(login_user: dict, req: KbPageReq) -> dict:
        """分页列表（POST /knowledge-bases/page）。"""
        async with mysql_client.get_session() as session:
            conds = [KnowledgeBase.is_deleted == 0]
            if req.name:
                conds.append(KnowledgeBase.kb_name.like(f"%{req.name}%"))
            if req.description:
                conds.append(KnowledgeBase.description.like(f"%{req.description}%"))
            if req.kb_type:
                conds.append(KnowledgeBase.kb_type == settings.check_kb_type(req.kb_type))
            if req.status is not None:
                conds.append(KnowledgeBase.status == req.status)
            visible = build_visible_cond(login_user, RESOURCE,
                                         KnowledgeBase.kb_id, KnowledgeBase.created_by)
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(KnowledgeBase).where(*conds))).scalar() or 0
            rows = (await session.execute(
                select(KnowledgeBase).where(*conds)
                .order_by(KnowledgeBase.kb_id.desc())
                .limit(req.size).offset((req.current - 1) * req.size))).scalars().all()
            items = [KnowledgeBaseService._to_dict(r) for r in rows]
            await decorate_actions(login_user, RESOURCE, items,
                                   owner_key=OWNER_KEY, session=session)
            await attach_creator(items, id_key=OWNER_KEY, session=session)
            return {"records": items, "total": total,
                    "current": req.current, "size": req.size}

    @staticmethod
    async def detail(login_user: dict, kb_id: int) -> dict:
        """知识库详情（配置面板回显：三份 JSON 全是归一后的完整配置）。"""
        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.require(login_user, kb_id, ACTION_VIEW,
                                                    session=session)
            data = KnowledgeBaseService._to_dict(kb)
            # 模型名只在详情里补：列表要补就得按行数批量查一遍模型表，页面用不上这个字段
            conf = await RagModelService.get_config(kb.embedding_model_id)
            data["embedModelName"] = conf.name if conf else None
            items = [data]
            await decorate_actions(login_user, RESOURCE, items,
                                   owner_key=OWNER_KEY, session=session)
            await attach_creator(items, id_key=OWNER_KEY, session=session)
            return items[0]

    @staticmethod
    async def options(login_user: dict) -> list[dict]:
        """可使用的知识库下拉（工作流知识库节点、检索页库选择都吃这个）。

        口径是 use 而不是 view：能被引用不等于能改配置，反之只被授了 view 的人
        在这里选不到它，比让他在节点里选了之后报 403 好懂。
        """
        async with mysql_client.get_session() as session:
            conds = [KnowledgeBase.is_deleted == 0, KnowledgeBase.status == 1]
            visible = build_visible_cond(login_user, RESOURCE,
                                         KnowledgeBase.kb_id, KnowledgeBase.created_by,
                                         action=ACTION_USE)
            if visible is not None:
                conds.append(visible)
            rows = (await session.execute(
                select(KnowledgeBase).where(*conds)
                .order_by(KnowledgeBase.kb_id.desc()))).scalars().all()
            return [{"id": r.kb_id, "name": r.kb_name, "kbType": r.kb_type,
                     "kbTypeLabel": RC.KB_TYPE_LABELS.get(r.kb_type, r.kb_type),
                     "embeddingModelId": r.embedding_model_id,
                     "graphEnabled": bool(r.graph_enabled), "docCount": r.doc_count}
                    for r in rows]

    # ==================== 新建 ====================
    @staticmethod
    async def create(login_user: dict, req: KbSaveReq) -> int:
        """创建知识库：类型校验 → 模型校验 → 三份配置归一 → 落库（不建任何存储对象）。

        ES 单索引在应用启动时幂等建好（common_es.ensure_index），图片型/音视频型也写同一个
        索引，所以建库不需要碰 ES；Neo4j 也不需要建库级容器（靠 kb_id 属性隔离）。
        """
        kb_type = settings.check_kb_type(req.kb_type)
        name = (req.name or "").strip()
        if not name:
            raise ValueError("知识库名称不能为空")
        # 模型校验在前：选了个 text_rerank 当向量模型，要等第一篇文档向量化时才炸，
        # 而那时用户已经传了文件、队列里也排上了任务，排查成本比建库时高一个量级
        checked = await RagModelService.validate_kb_models(
            kb_type, embed_model_id=req.embed_model_id,
            rerank_model_id=req.rerank_model_id or 0, chat_model_id=req.chat_model_id or 0,
            enable_graph=bool(req.enable_graph))
        dim = RagModelService.embedding_dim(checked["embed"])

        engine, parse_config = settings.normalize_parse_config(
            kb_type, req.parser_engine, req.parse_config)
        chunk_config = settings.normalize_chunk_config(
            req.chunk_config, chunk_size=req.chunk_size, chunk_overlap=req.chunk_overlap)
        retrieve_config = settings.normalize_retrieve_config(
            req.retrieve_config, top_k=req.top_k, score_threshold=req.score_threshold)
        graph_enabled = settings.normalize_graph_enabled(kb_type, req.enable_graph)

        async with mysql_client.get_session() as session:
            if (await session.execute(select(KnowledgeBase.kb_id).where(
                    KnowledgeBase.kb_name == name,
                    KnowledgeBase.kb_type == kb_type,
                    KnowledgeBase.is_deleted == 0))).first():
                raise ValueError(f"已存在同名的{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}：{name}")
            kb = KnowledgeBase(
                kb_name=name, description=req.description, kb_type=kb_type,
                org_id=settings.org_id(),
                embedding_model_id=req.embed_model_id, embedding_dim=dim,
                rerank_model_id=req.rerank_model_id or 0, chat_model_id=req.chat_model_id or 0,
                parser_engine=engine, parse_config=parse_config,
                chunk_config=chunk_config, retrieve_config=retrieve_config,
                graph_enabled=graph_enabled, status=1,
                owner_dept_id=int(login_user.get("dept_id") or 0),
                kb_metadata=req.metadata,
                created_by=int(login_user.get("user_id") or 0))
            session.add(kb)
            await session.flush()
            kb_id = kb.kb_id
            await session.commit()
        log.info("知识库已创建: id={} name={} type={} engine={} graph={}",
                 kb_id, name, kb_type, engine, graph_enabled)
        return kb_id

    # ==================== 修改 ====================
    @staticmethod
    async def modify(login_user: dict, kb_id: int, req: KbSaveReq) -> bool:
        """修改知识库（名称/描述/模型/解析与分块配置）。

        拒绝改 kb_type 与 embedding_model_id；解析或分块配置真变了才 version+1，
        页面据此提示「配置已更新，N 份文档需要重新解析」。
        """
        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.require(login_user, kb_id, ACTION_EDIT,
                                                    session=session)
            new_type = settings.check_kb_type(req.kb_type or kb.kb_type)
            if new_type != kb.kb_type:
                raise ValueError(f"知识库类型创建后不可修改（当前 {kb.kb_type}）")
            if req.embed_model_id and req.embed_model_id != kb.embedding_model_id:
                raise ValueError("向量模型创建后不可修改：换模型等于换向量空间，"
                                 "历史切片全部作废，请新建知识库")

            name = (req.name or "").strip() or kb.kb_name
            if name != kb.kb_name and (await session.execute(
                    select(KnowledgeBase.kb_id).where(
                        KnowledgeBase.kb_name == name,
                        KnowledgeBase.kb_type == kb.kb_type,
                        KnowledgeBase.kb_id != kb_id,
                        KnowledgeBase.is_deleted == 0))).first():
                raise ValueError(f"已存在同名的{RC.KB_TYPE_LABELS.get(kb.kb_type, kb.kb_type)}：{name}")

            checked = await RagModelService.validate_kb_models(
                kb.kb_type, embed_model_id=kb.embedding_model_id,
                rerank_model_id=req.rerank_model_id or 0, chat_model_id=req.chat_model_id or 0,
                enable_graph=bool(req.enable_graph) if req.enable_graph is not None
                else bool(kb.graph_enabled))
            if checked["dim"] != kb.embedding_dim:
                raise ValueError(f"向量模型维度与知识库不一致（知识库 {kb.embedding_dim} 维）")

            engine, parse_config = settings.normalize_parse_config(
                kb.kb_type, req.parser_engine or kb.parser_engine,
                req.parse_config if req.parse_config is not None else kb.parse_config)
            chunk_config = settings.normalize_chunk_config(
                req.chunk_config if req.chunk_config is not None else kb.chunk_config,
                chunk_size=req.chunk_size, chunk_overlap=req.chunk_overlap)
            retrieve_config = settings.normalize_retrieve_config(
                req.retrieve_config if req.retrieve_config is not None else kb.retrieve_config,
                top_k=req.top_k, score_threshold=req.score_threshold)
            graph_enabled = settings.normalize_graph_enabled(
                kb.kb_type, kb.graph_enabled if req.enable_graph is None else req.enable_graph)

            # 只有会让已有切片作废的改动才推版本号（检索配置与名称不参与解析）
            reindex = (engine != kb.parser_engine
                       or parse_config != (kb.parse_config or {})
                       or chunk_config != (kb.chunk_config or {}))
            values = {
                "kb_name": name, "description": req.description if req.description is not None
                else kb.description,
                "rerank_model_id": req.rerank_model_id or 0,
                "chat_model_id": req.chat_model_id or 0,
                "parser_engine": engine, "parse_config": parse_config,
                "chunk_config": chunk_config, "retrieve_config": retrieve_config,
                "graph_enabled": graph_enabled,
                "kb_metadata": req.metadata if req.metadata is not None else kb.kb_metadata,
                "update_by": int(login_user.get("user_id") or 0),
            }
            if reindex:
                values["version"] = kb.version + 1
            await session.execute(update(KnowledgeBase).where(
                KnowledgeBase.kb_id == kb_id).values(**values))
            await session.commit()
        log.info("知识库已修改: id={} 需重解析={} version->{}", kb_id, reindex,
                 (kb.version + 1) if reindex else kb.version)
        return True

    @staticmethod
    async def switch_status(login_user: dict, kb_id: int, enabled: bool) -> bool:
        """停用/启用：停用后检索与下拉里消失，数据与 ES 向量全部保留（不是删除的轻量替代）。"""
        async with mysql_client.get_session() as session:
            await KnowledgeBaseService.require(login_user, kb_id, ACTION_EDIT, session=session)
            await session.execute(update(KnowledgeBase).where(
                KnowledgeBase.kb_id == kb_id).values(
                    status=1 if enabled else 0,
                    update_by=int(login_user.get("user_id") or 0)))
            await session.commit()
        log.info("知识库状态切换: id={} enabled={}", kb_id, enabled)
        return True

    # ==================== 删除 ====================
    @staticmethod
    async def remove(login_user: dict, kb_id: int) -> bool:
        """删除知识库：MySQL 软删 + 异步清 ES/Neo4j/存储原件。

        库里可能有几万条切片，同步 delete_by_query 会把 HTTP 请求挂到超时，所以底层清理
        投 rag 流水线（RAG_TASK_KB_PURGE）；接口返回即业务上不可见，软删行留着便于回查。
        """
        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.require(login_user, kb_id, ACTION_DELETE,
                                                    session=session)
            doc_ids = [int(i) for i in (await session.execute(
                select(Document.doc_id).where(Document.kb_id == kb_id))).scalars().all()]
            await session.execute(update(Document).where(
                Document.kb_id == kb_id, Document.is_deleted == 0).values(
                    is_deleted=1, update_by=int(login_user.get("user_id") or 0)))
            await session.execute(update(KnowledgeBase).where(
                KnowledgeBase.kb_id == kb_id).values(
                    is_deleted=1, doc_count=0, chunk_count=0,
                    update_by=int(login_user.get("user_id") or 0)))
            await session.commit()
        task_id = await RagTaskService.enqueue(
            RC.RAG_TASK_KB_PURGE, [kb_id], job_key=f"purge:{kb_id}")
        log.info("知识库已删除: id={} 连带文档={} purge_task={}", kb_id, len(doc_ids), task_id)
        return True

    # ==================== 计数回写 ====================
    @staticmethod
    async def refresh_counters(kb_id: int, session: Optional[AsyncSession] = None) -> dict:
        """按真实数据回写 doc_count / chunk_count（摄取与删除链路收尾时调用）。

        这两个数是「派生值」，不做增量累加：增量要么漏（异常分支没走到）要么重
        （任务重试过），一旦对不上，页面显示的分块总数就再也无法自证清白。
        """
        async with maybe_session(session) as s:
            doc_count = (await s.execute(
                select(func.count()).select_from(Document).where(
                    Document.kb_id == kb_id, Document.is_deleted == 0))).scalar() or 0
            chunk_count = (await s.execute(
                select(func.count()).select_from(DocumentChunk).where(
                    DocumentChunk.kb_id == kb_id))).scalar() or 0
            await s.execute(update(KnowledgeBase).where(
                KnowledgeBase.kb_id == kb_id).values(
                    doc_count=doc_count, chunk_count=chunk_count))
            await s.commit()
            return {"doc_count": doc_count, "chunk_count": chunk_count}

    # ==================== 序列化 ====================
    @staticmethod
    def _to_dict(row: KnowledgeBase) -> dict:
        """ORM 行 → 前端契约字典（配置读时归一，NULL 老行也能给出完整面板）。"""
        engine, parse_config = KnowledgeBaseService.parse_of(row)
        chunk_config = KnowledgeBaseService.chunk_of(row)
        retrieve_config = KnowledgeBaseService.retrieve_of(row)
        resp = KnowledgeBaseResp(
            id=row.kb_id, name=row.kb_name, description=row.description,
            kb_type=row.kb_type, kb_type_label=RC.KB_TYPE_LABELS.get(row.kb_type, row.kb_type),
            tenant_id=row.org_id, collection_name=RC.RAG_CHUNK_INDEX,
            embed_model_id=row.embedding_model_id, embedding_dim=row.embedding_dim,
            rerank_model_id=row.rerank_model_id, chat_model_id=row.chat_model_id,
            top_k=retrieve_config["top_k"],
            score_threshold=retrieve_config["score_threshold"],
            chunk_size=chunk_config["chunk_size"], chunk_overlap=chunk_config["chunk_overlap"],
            chunk_strategy=chunk_config["strategy"],
            enable_graph=bool(row.graph_enabled), parser_engine=engine,
            parse_config=parse_config, chunk_config=chunk_config,
            retrieve_config=retrieve_config,
            version=row.version, doc_count=row.doc_count, chunk_count=row.chunk_count,
            status=row.status, metadata=row.kb_metadata, created_by=row.created_by,
            create_time=_fmt_time(row.create_time), update_time=_fmt_time(row.update_time))
        return resp.dump()

    @staticmethod
    def pick(row: KnowledgeBase, *fields: str) -> dict[str, Any]:
        """给内部链路（摄取/检索/图谱）取几个字段的轻量字典，避免为读配置加载整行 ORM。"""
        data = {"kb_id": row.kb_id, "kb_type": row.kb_type, "org_id": row.org_id,
                "version": row.version, "status": row.status,
                "graph_enabled": bool(row.graph_enabled)}
        for f in fields:
            data[f] = getattr(row, f, None)
        return data


def _fmt_time(value) -> Optional[str]:
    """DATETIME → 前端展示串（与 skill/workflow 列表同口径）。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None

