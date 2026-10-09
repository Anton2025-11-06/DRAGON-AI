# -*- coding: utf-8 -*-
"""文档与切片管理：上传落存储、列表与双维度进度、重试/构建向量/构建图谱/删除、
预览原文件、切片编辑与删除。

与 kb_service / task_service / parse_service 的分工
----------------------------------------------------
本模块只做「API 侧对文档行与切片行的读写 + 鉴权 + 入队」，一律不在请求线程里解析文件、
算向量、写 ES——那是 rag worker 里 parse_service 的活（SPEC §4）。唯一的例外是
「改单个切片正文」后附带的那一次重新向量化：一个 HTTP 往返、只动一条切片，为它排一次
异步任务，反而更容易出现「页面已改但索引还是旧的」这种长时间说不清的不一致。

四条硬口径
----------
1. **对象名可反推**：原件 ``raw/{kb_id}/{doc_id}.{ext}``（落在 storage.path_prefix=rag/ 之下，
   SPEC §11.2-1）。名字由 doc_id 决定，所以先建行拿到 id、再落文件、最后回写 file_path；
   文件落不下去就把行一起删掉——留一篇没有原件的文档，重试永远失败，还占着列表位。
   反过来也一样：一批没走完（批级 commit 挂了），已经落上去的原件要清掉，
   别在存储里留一堆再也没人指向的对象。
2. **权限两层各管一段**：能不能往库里传文件按知识库资源的 ``upload`` 动作；文档行的
   查看/重跑/改正文/删除按文档资源自己的 ACL（归属人 = 文档 created_by）。列表页两层
   各下一遍条件，不会出现「库进得去、别人的文档也改得动」。
3. **批量逐项回执**：上传与重试一批文档时，一个文件失败不回滚其它文件（批量场景里
   成 10 个失败 2 个是常态）。上传按批走（一批建行 + 并发落存储 + 并发入队）而不是
   逐文件串行——多选上传的耗时几乎全在等上一个文件的网络往返；回执仍然一项一个文件。
4. **列表不返回正文**：切片完整正文只在 tb_document_chunk（SPEC §10.2 里 ES 的 content
   是给检索用的副本），列表页要看得动就翻页到切片管理，避免一次列表拖几百 KB。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, or_, select, update

from common.common_constants import rag_constant as RC
from common.common_es import es_client, to_json
from common.common_es.index_mapping import es_doc_id
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_neo4j import kg_store
from common.common_permission.resource_guard import (
    ACTION_CHUNK, ACTION_CONTENT, ACTION_DELETE, ACTION_EDIT, ACTION_GRAPH, ACTION_PREVIEW,
    ACTION_REPARSE, ACTION_UPLOAD, ACTION_VIEW, KB_FILE_TO_DOC, build_visible_cond,
    decorate_actions, ensure_action, granted_actions, maybe_session, spec_of,
)
from common.common_storage import MAX_FILE_SIZE, get_storage
from common.common_utils.creator_util import attach_creator
from service.service_rag.models.kb_entity import Document, DocumentChunk, KnowledgeBase
from service.service_rag.schemas.rag_schema import (
    ChunkPageReq, ChunkResp, ChunkUpdateReq, BuildVectorReq, DocPageReq, DocProgressResp,
    DocumentResp,
)
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.parse_service import obj_raw_name
from service.service_rag.services.kb_service import KnowledgeBaseService
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.task_service import RagTaskService

# 两层资源码（与 resource_guard 的 RESOURCE_SPECS 键一致）
RESOURCE_KB = "knowledge_base"
RESOURCE_DOC = "document"
# dump() 出来的行是 camelCase，授权与创建人补全按这份键名取归属列
OWNER_KEY = "createdBy"

# 标题与文件名的列宽（tb_document 的 String 长度，落库前按它截断，不让 MySQL 报 1406）
TITLE_MAX = 255
FILE_NAME_MAX = 255
MIME_MAX = 128

# 批量上传的节流：一批多少个文件、一批里并发多少路。分批是为了别让一次请求在 MySQL 上
# 挂着长事务（开放 API 单次可达 100 个文件）；并发上限同时就是「最多有多少份原件停在
# 内存里」——存储 PUT 慢在往返，并发几路就已接近吃满带宽，再开高也不会更快
UPLOAD_BATCH = 8
UPLOAD_CONCURRENCY = 6


class RagDocService:
    """文档与切片的管理层（全静态方法，router 直接 await）"""

    # ==================== 一、加载与鉴权 ====================

    @staticmethod
    async def load(session, doc_id: int, *, alive: bool = True) -> Optional[Document]:
        """按 id 取文档行；alive=False 时连软删行一起给（删除后的清理链路还要读它）。"""
        conds = [Document.doc_id == int(doc_id)]
        if alive:
            conds.append(Document.is_deleted == 0)
        return (await session.execute(select(Document).where(*conds))).scalar_one_or_none()

    @staticmethod
    async def require(login_user: dict, doc_id: int, action: str, *,
                      session=None) -> Document:
        """取行 + 卡动作（文档级鉴权的唯一入口，与 kb_service.require 同一个套路）。

        parent 传所属知识库：知识库那一行上勾的「库内文件」那组动作对整库文档生效
        （只兜 KB_FILE_TO_DOC 里那几个码，库上的 edit/delete/share 兜不过来）。
        """
        async with maybe_session(session) as s:
            doc = await RagDocService.load(s, doc_id)
            if not doc:
                raise ValueError("文档不存在")
            await ensure_action(login_user, RESOURCE_DOC, doc_id, action,
                                owner_id=doc.created_by, session=s,
                                parent=(RESOURCE_KB, doc.kb_id))
            return doc

    @staticmethod
    async def _require_kb(login_user: dict, kb_id: int, action: str, session
                          ) -> KnowledgeBase:
        """卡知识库层的动作（上传、删除、构建向量都先过这一关）。"""
        return await KnowledgeBaseService.require(login_user, kb_id, action, session=session)

    @staticmethod
    async def _union_kb_actions(login_user: dict, kb_id: int, items: list[dict], *,
                                session) -> None:
        """把知识库那一行授到的「库内文件」动作并进每篇文档的行内按钮（整库一次查，不按行查）。

        必须与 ensure_action 的父库兜底同口径：判得过却不让按钮亮，人只能靠猜；反过来
        按钮亮了点下去 403 更糟。两处都读 KB_FILE_TO_DOC 这一份映射，不会漂成两个规则。
        归属人/ADMIN 已经拿到全集（decorate_actions 里给的），多并一次也是同一个集合。
        """
        if not items:
            return
        granted = await granted_actions(login_user, RESOURCE_KB, kb_id, session=session)
        extra = {KB_FILE_TO_DOC[a] for a in granted if a in KB_FILE_TO_DOC}
        if not extra:
            return
        order = list(spec_of(RESOURCE_DOC)["actions"])
        for it in items:
            acts = set(it.get("actions") or ()) | extra
            # 按后端清单顺序重排：行内按钮的显隐顺序就是这一份列表的顺序，不能插队
            it["actions"] = [a for a in order if a in acts] or [ACTION_VIEW]

    # ==================== 二、列表与进度 ====================

    @staticmethod
    async def page(login_user: dict, kb_id: int, req: DocPageReq) -> dict:
        """文档分页（三类知识库共用一个列表口，预览形态由返回的 mediaType 决定）。"""
        async with mysql_client.get_session() as session:
            # 只借这一步鉴权（看得到这个库才看得到它的文档），行本身列表里用不上
            await RagDocService._require_kb(login_user, kb_id, ACTION_VIEW, session)
            conds = [Document.kb_id == int(kb_id), Document.is_deleted == 0]
            if req.name:
                # 标题与原始文件名都算命中：列表上显示的是「改过的标题，没改过就是文件名」，
                # 搜索必须能搜到列上看到的这串字，只按 doc_name 筛会让没改过标题的文档搜不到
                kw = f"%{req.name}%"
                conds.append(or_(Document.doc_name.like(kw), Document.file_name.like(kw)))
            if req.status:
                if req.status not in RC.DOC_STATUS_ALL:
                    raise ValueError(f"未知的解析状态：{req.status}")
                conds.append(Document.status == req.status)
            if req.graph_state is not None:
                conds.append(Document.graph_state == int(req.graph_state))
            visible = build_visible_cond(login_user, RESOURCE_DOC,
                                         Document.doc_id, Document.created_by,
                                         parent=(RESOURCE_KB, Document.kb_id, ACTION_CONTENT))
            if visible is not None:
                conds.append(visible)
            total = (await session.execute(
                select(func.count()).select_from(Document).where(*conds))).scalar() or 0
            rows = (await session.execute(
                select(Document).where(*conds)
                .order_by(Document.doc_id.desc())
                .limit(req.size).offset((req.current - 1) * req.size))).scalars().all()
            items = [RagDocService._to_dict(r) for r in rows]
            await decorate_actions(login_user, RESOURCE_DOC, items,
                                   owner_key=OWNER_KEY, session=session)
            await RagDocService._union_kb_actions(login_user, kb_id, items, session=session)
            await attach_creator(items, id_key=OWNER_KEY, session=session)
            # 列表不再补签名地址：图片墙/播放器改成表格后页面不再自动加载多媒体，
            # 预览统一走 original_preview（那一口卡 preview，签地址也是当场签当场用）。
            # 逐行 public_url 是串行的，一页十几行就是十几次往返，白摊在每次列表刷新上。
            return {"records": items, "total": total,
                    "current": req.current, "size": req.size}

    @staticmethod
    async def detail(login_user: dict, doc_id: int) -> dict:
        """文档详情（含解析引擎、配置版本与实际用掉的切片数）。"""
        async with mysql_client.get_session() as session:
            doc = await RagDocService.require(login_user, doc_id, ACTION_VIEW, session=session)
            kb = await KnowledgeBaseService.load(session, doc.kb_id)
            data = RagDocService._to_dict(doc)
            items = [data]
            await decorate_actions(login_user, RESOURCE_DOC, items,
                                   owner_key=OWNER_KEY, session=session)
            await RagDocService._union_kb_actions(login_user, doc.kb_id, items, session=session)
            await attach_creator(items, id_key=OWNER_KEY, session=session)
            await RagDocService._attach_media_urls(
                kb, items, {int(doc.doc_id): str(doc.file_path or "")})
            return items[0]

    @staticmethod
    def _progress_dict(doc_id: int, status: Optional[str], graph_state: Any,
                       pair: dict[str, Any]) -> dict:
        """两个状态列（MySQL）+ 两份进度（Redis）→ 一条进度回执。

        单篇轮询与整屏批量共用这一个出口：两处各拼一份 update 字典，早晚拼出两个口径
        （批量少拼一列，页面就在「一条 SQL」之后显示出和单条不一样的进度）。
        """
        vector = dict(pair.get(RC.PROGRESS_SCOPE_VECTOR) or {})
        graph = pair.get(RC.PROGRESS_SCOPE_GRAPH) or {}
        graph_state = int(graph_state or 0)
        vector.update({
            "doc_id": int(doc_id),
            "status": status,
            "status_label": RC.DOC_STATUS_LABELS.get(status, status),
            "graph_stage": str(graph.get("stage") or ""),
            "graph_stage_label": graph.get("stage_label"),
            "graph_percent": int(graph.get("percent") or 0),
            "graph_total": int(graph.get("total") or 0),
            "graph_done": int(graph.get("done") or 0),
            "graph_message": graph.get("message"),
            "graph_stages": graph.get("stages") or [],
            "graph_state": graph_state,
            "graph_state_label": RC.KG_STATE_LABELS.get(graph_state, None),
        })
        return DocProgressResp(**{k: v for k, v in vector.items()
                                  if k in DocProgressResp.model_fields}).dump()

    @staticmethod
    async def progress(login_user: dict, doc_id: int) -> dict:
        """双维度进度（页面轮询这一口；状态取 MySQL，两条进度取 Redis 各自的键）。

        向量化与图谱各自一段进度条：“文档向量化完了、图谱正在重抽”同时存在，
        拿一份进度盖两个维度就会把图谱进度条显示成 100% 或倒回去。
        finished 以 MySQL 为凭（PROCESSED / graph_state=DONE），不依赖 Redis 里的残留。
        """
        async with mysql_client.get_session() as session:
            doc = await RagDocService.require(login_user, doc_id, ACTION_VIEW, session=session)
            pair = await RagTaskService.progress_pair(
                doc_id,
                vector_finished=(doc.status == RC.DOC_STATUS_PROCESSED),
                graph_finished=(int(doc.graph_state or 0) == RC.KG_STATE_DONE))
        return RagDocService._progress_dict(doc_id, doc.status, doc.graph_state, pair)

    @staticmethod
    async def progress_many(login_user: dict, doc_ids: Sequence[int]) -> list[dict]:
        """批量进度（列表页一次刷整屏）：一条 SQL 拉回整屏的状态列，再一次 pipeline 拉回
        2N 份进度，替掉原来「每行开一个会话、查一行、鉴一次权、读两次 Redis」的 N 倍往返。

        鉴权按 view 动作下推成 SQL 条件（与 page() 列表同一个口径），所以：
        - 行被别人删了、或者本来就不在当前用户的可见集合里：少返回一行，整屏照样刷得出来
          （逐行 require 时「无权限」抛的是 UnauthorizedException，会顶掉整个批量口）；
        - 只取进度要用的三列，不把 doc_name 与那几份配置 JSON 一起拖回来。
        返回顺序与入参 doc_ids 一致，前端按行盖进度时不必再排一次。
        """
        ids: list[int] = []
        for one in doc_ids:
            try:
                doc_id = int(one)
            except (TypeError, ValueError):
                continue
            if doc_id > 0 and doc_id not in ids:
                ids.append(doc_id)
        if not ids:
            return []
        async with mysql_client.get_session() as session:
            conds = [Document.doc_id.in_(ids), Document.is_deleted == 0]
            visible = build_visible_cond(login_user, RESOURCE_DOC,
                                         Document.doc_id, Document.created_by,
                                         parent=(RESOURCE_KB, Document.kb_id, ACTION_CONTENT))
            if visible is not None:
                conds.append(visible)
            rows = (await session.execute(
                select(Document.doc_id, Document.status, Document.graph_state)
                .where(*conds))).all()
        states = {int(r[0]): (r[1], int(r[2] or 0)) for r in rows}
        if not states:
            return []
        pairs = await RagTaskService.progress_pair_many(
            ids,
            vector_finished={k: v[0] == RC.DOC_STATUS_PROCESSED for k, v in states.items()},
            graph_finished={k: v[1] == RC.KG_STATE_DONE for k, v in states.items()})
        return [RagDocService._progress_dict(doc_id, states[doc_id][0], states[doc_id][1],
                                             pairs.get(doc_id) or {})
                for doc_id in ids if doc_id in states]

    @staticmethod
    async def content_preview(login_user: dict, doc_id: int) -> dict:
        """正文预览（按 chunk_index 顺序拼该文档的**全部**切片，不截断）。

        只读 MySQL 的切片表，不回 ES：预览要看的是「我们存下来的原文」，
        而 ES 那份是为检索准备的副本（可能被截断、也可能还没刷盘）。

        带媒体切片在正文里补一条地址（需求 13 的「地址回填到多媒体原位置」）：
        库里存的是对象名，签名地址会过期，所以只能在这一刻现签，不进也不落库。
        排版是**地址在上、描述在下**：先看得到图，再读它说明什么。
        音视频不用 markdown 图片语法（那不是个能播的东西），给一条可点开的链接。

        ``truncated`` 恒为 False：字段留着只为了兼容已经上线的前端，不再有意义。
        大文档（几万片）一次拼回全部正文会同时占住内存与响应体，真出问题就
        在这里加回长度上限。
        """
        doc = await RagDocService.require(login_user, doc_id, ACTION_VIEW)
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(DocumentChunk.content, DocumentChunk.media_url)
                .where(DocumentChunk.doc_id == int(doc_id))
                .order_by(DocumentChunk.chunk_index.asc()))).all()
        parts: list[str] = []
        for one_text, media in rows:
            body = str(one_text or "")
            url = await _sign(str(media or "")) if media else None
            if url:
                ref = str(media or "").rsplit("?", 1)[0]
                ext = ref.rsplit(".", 1)[-1].lower() if "." in ref else ""
                kind = RC.media_kind_of_ext(ext) or RC.MEDIA_KIND_IMAGE
                label = RC.MEDIA_KIND_LABELS.get(kind, "图片")
                # 图片走内联语法（渲染视图里能真看见），音视频只给链接：
                # 写成 ![视频](url) 在 markdown 里只会退成一句 Alt 文字，不是能播的播放器
                head = (f"![{label}]({url})" if kind == RC.MEDIA_KIND_IMAGE
                        else f"[{label}]({url})")
                # 没增强时切片正文就是那一句占位词（[图片]/[音频]/[视频]），
                # 与地址行一并写出来就是同一句话说两遍
                if body.strip() in RC.MEDIA_KIND_PLACEHOLDERS.values():
                    body = ""
                body = f"{head}\n{body}" if body.strip() else head
            if body:
                parts.append(body)
        text = "\n\n".join(parts)
        return {"docId": int(doc_id), "docName": doc.doc_name,
                "totalChunks": int(doc.chunk_count or 0),
                "truncated": False, "content": text}

    @staticmethod
    async def original_preview(login_user: dict, doc_id: int) -> dict:
        """预览原文件（列表行上的「预览原文件」，卡 preview 动作）。不卡 use/edit。

        单独一个口而不全担列表里的 mediaUrl：预览是要鉴权的动作，拿对象名直接拼地址
        等于绕过 ACL；前端拿到 mediaType 才能决定用 img / video / iframe / 下载。
        """
        async with mysql_client.get_session() as session:
            doc = await RagDocService.require(login_user, doc_id, ACTION_PREVIEW, session=session)
            kb = await KnowledgeBaseService.load(session, doc.kb_id)
            # commit/close 后 ORM 行就过期了，要用的列一次性取成局部变量再出会话
            snapshot = {"docName": doc.doc_name, "fileName": doc.file_name,
                        "fileExt": doc.file_ext, "fileSize": int(doc.file_size or 0),
                        "contentType": doc.content_type}
            object_name = str(doc.file_path or "")
            media_type = str(doc.media_type or RC.KB_TYPE_MEDIA_TYPE.get(
                kb.kb_type if kb else "", RC.MEDIA_TYPE_TEXT))
        url = await _sign(object_name)
        if not url:
            raise ValueError("原件访问地址生成失败（存储后端不可用或对象已不存在）")
        return {"docId": int(doc_id), **snapshot, "mediaType": media_type, "url": url}

    # ==================== 三、上传（页面 + 开放 API） ====================

    @staticmethod
    async def upload(login_user: dict, kb_id: int, files: Sequence[Any], *,
                     open_api: bool = False) -> dict:
        """批量上传：按库类型卡格式，分批建行 + 并发落存储 + 并发入队（不整批回滚）。

        :param files: FastAPI 的 UploadFile 序列（开放 API 同样是 multipart）
        :param open_api: True 时按 ``rag_constant.OPEN_API_MAX_FILES`` 限单次文件数（当前 100）——
            那是给脚本用的软门槛；页面一次拖多少个文件属于人在操作，交给前端自己控制更合理
        """
        items = list(files or [])
        if not items:
            raise ValueError("没有收到任何文件")
        async with mysql_client.get_session() as session:
            kb = await RagDocService._require_kb(login_user, kb_id, ACTION_UPLOAD, session)
        if open_api:
            limit = settings.open_api_max_files()
            if len(items) > limit:
                raise ValueError(f"开放 API 单次最多上传 {limit} 个文件（当前 {len(items)} 个）")
        allowed = [str(e).lower() for e in RC.KB_TYPE_ALLOWED_EXTS.get(kb.kb_type, [])]

        results: list[dict] = []
        # 分批走：一批一次事务、一批一次并发，既拿到批量的收益，又不至于在
        # 开放 API 单次 100 个文件时把事务时长与内存驻留一起拉满
        for start in range(0, len(items), UPLOAD_BATCH):
            results.extend(await RagDocService._save_more(
                kb, items[start:start + UPLOAD_BATCH], allowed, login_user))
        accepted = sum(1 for r in results if r.get("docId"))
        message = "；".join(f"{r['fileName']}: {r['message']}"
                            for r in results if r.get("message"))
        return {"kbId": int(kb.kb_id), "accepted": accepted,
                "rejected": len(results) - accepted, "items": results,
                "message": message[:500] or None}

    @staticmethod
    async def _save_more(kb: KnowledgeBase, files: Sequence[Any], allowed: Sequence[str],
                         login_user: dict) -> list[dict]:
        """一小批文件：校验 → 一次建行拿齐 doc_id → 并发落存储 → 一次提交回写路径 → 并发入队。

        取代逐文件串行的写法：12 张图原本是 12 轮「开会话 + INSERT + PUT + UPDATE + 排队」
        串着跑，每一轮都在等上一个文件的往返。现在 DB 只剩批量的几次，真正耗时的
        存储 PUT 与排队按 ``UPLOAD_CONCURRENCY`` 并发。

        顺序仍不能调：对象名里带 doc_id，没建行就没有名字；文件落不下去就把刚建的行
        一并标删，否则留下一条永远解析失败的文档，用户只能在列表里看着它。

        逐项隔离的口径不变：批里某个文件读流/落存储/入队失败，只摘它自己的行、
        只影响它自己的回执（开放 API 单次 100 个文件时，成 98 败 2 是常态不是异常）。
        """
        creator = int(login_user.get("user_id") or 0)
        # 默认按库型定展示形态；doc 型库现在也收图片/音频/视频，这类行改按扩展名写，
        # 否则预览口拿到的 mediaType 恒是 text，前端不知道该用 img / audio / video 哪个播放器
        media_type = RC.KB_TYPE_MEDIA_TYPE.get(kb.kb_type, RC.MEDIA_TYPE_TEXT)
        receipts: dict[int, dict] = {}
        pending: list[dict] = []
        for idx, one in enumerate(files):
            name = os.path.basename(
                str(getattr(one, "filename", "") or "").replace("\\", "/")).strip()
            base, ext = os.path.splitext(name)
            ext = ext.lstrip(".").lower()
            reason = RagDocService._reject_reason(kb, ext, allowed, one)
            if reason:
                receipts[idx] = RagDocService._reject(name or "untitled", reason)
                continue
            pending.append({
                "idx": idx, "name": name, "ext": ext,
                "title": (base or "").strip() or "untitled",
                "file": one,
                "mime": str(getattr(one, "content_type", "") or "")[:MIME_MAX] or None,
                "mediaType": RC.doc_media_type_of(ext) or media_type,
            })
        if not pending:
            return [receipts[i] for i in range(len(files))]

        sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
        try:
            async with mysql_client.get_session() as session:
                for p in pending:
                    doc = Document(
                        kb_id=int(kb.kb_id), doc_name=p["title"][:TITLE_MAX],
                        file_name=p["name"][:FILE_NAME_MAX],
                        file_path="",  # 拿到 doc_id 才能算对象名，落成功后再回写
                        file_ext=p["ext"][:16],
                        file_size=0,   # 真实大小要读到字节才知道，同上一起回写
                        content_type=p["mime"], media_type=p["mediaType"],
                        status=RC.DOC_STATUS_PENDING, parse_version=int(kb.version or 0),
                        created_by=creator)
                    session.add(doc)
                    p["doc"] = doc
                await session.flush()  # 一批 INSERT 拿齐自增 doc_id
                for p in pending:
                    # 下游只在会话内用 ORM 对象，doc_id 先落字典：会话一关属性就取不到了
                    p["docId"] = int(p["doc"].doc_id)
                await asyncio.gather(
                    *(RagDocService._store_one(sem, kb, p) for p in pending))
                dropped: list[int] = []
                for p in pending:
                    if p.get("error"):
                        dropped.append(int(p["docId"]))
                        continue
                    p["doc"].file_path = p["objectName"]
                    p["doc"].file_size = int(p["size"])
                if dropped:
                    # 与旧的单文件回滚同一口径（标删不物删），只是一条语句批掉
                    await session.execute(update(Document).where(
                        Document.doc_id.in_(dropped)).values(is_deleted=1))
                await session.commit()
        except Exception as e:  # noqa: BLE001  批级兜底：没走完的每个文件都给失败回执
            log.exception(f"批量上传落库失败 kb={kb.kb_id} 批={len(pending)}")
            # 事务会回滚，但已经 PUT 上去的原件不会跟着回滚：不删就是存储里没人指向的
            # 孤儿对象。改批量后一次 commit 失败最多会漏一批，比逐文件时口子更大，这里补上
            landed = [p["objectName"] for p in pending if p.get("objectName")]
            if landed:
                await asyncio.gather(*(RagDocService._drop_object(n) for n in landed),
                                     return_exceptions=True)
            for p in pending:
                receipts[p["idx"]] = RagDocService._reject(p["name"], f"保存失败：{str(e)[:150]}")
            return [receipts[i] for i in range(len(files))]

        # 入队放在提交之后：worker 拉起任务时文档行与原件必须已经可读
        await asyncio.gather(*(RagDocService._enqueue_one(sem, kb, p) for p in pending
                               if not p.get("error")))
        for p in pending:
            if p.get("error"):
                receipts[p["idx"]] = RagDocService._reject(p["name"], p["error"])
            elif p.get("queueError"):
                receipts[p["idx"]] = {"docId": p["docId"], "fileName": p["name"],
                                      "taskId": None, "status": RC.DOC_STATUS_FAILED,
                                      "message": p["queueError"]}
            else:
                log.info("文档已接收: kb={} doc={} file={} size={} task={}",
                         kb.kb_id, p["docId"], p["name"], p.get("size"), p.get("taskId"))
                receipts[p["idx"]] = {"docId": p["docId"], "fileName": p["name"],
                                      "taskId": p.get("taskId"),
                                      "status": RC.DOC_STATUS_PENDING, "message": None}
        return [receipts[i] for i in range(len(files))]

    @staticmethod
    def _reject_reason(kb: KnowledgeBase, ext: str, allowed: Sequence[str],
                       file: Any) -> Optional[str]:
        """建行前的零成本校验（不读文件内容）：没扩展名 / 格式不合库型 / 声明大小超限。"""
        if not ext:
            return "文件没有扩展名，无法判定解析链路"
        if ext not in allowed:
            # doc 型的白名单现在含图音视频，将近 30 个后缀全塞进一句话没人看完
            shown = ("/".join(allowed) if len(allowed) <= 10
                     else f"{'/'.join(allowed[:10])} 等 {len(allowed)} 种")
            return (f"{RC.KB_TYPE_LABELS.get(kb.kb_type, kb.kb_type)}只接受 "
                    f"{shown}（当前 .{ext}）")
        declared = getattr(file, "size", None)
        if declared and int(declared) > MAX_FILE_SIZE:
            return f"文件超过单文件上限 {MAX_FILE_SIZE // 1024 // 1024}MB"
        return None

    @staticmethod
    async def _store_one(sem: "asyncio.Semaphore", kb: KnowledgeBase, p: dict) -> None:
        """读流 + 落存储（受 sem 限并发的那一段）。失败只记在 p 上，不外抛牵连同批。"""
        async with sem:
            try:
                data = await p["file"].read()
            except Exception as e:  # noqa: BLE001  读流失败（客户端断开等）只影响这一个文件
                p["error"] = f"读取上传流失败：{str(e)[:120]}"
                return
            finally:
                p["file"] = None  # 抽掉引用：一批里最多只留 UPLOAD_CONCURRENCY 份原件在内存
            if not data:
                p["error"] = "文件内容为空"
                return
            if len(data) > MAX_FILE_SIZE:
                p["error"] = (f"文件超过单文件上限 "
                              f"{MAX_FILE_SIZE // 1024 // 1024}MB")
                return
            object_name = obj_raw_name(kb.kb_id, int(p["docId"]), p["ext"])
            try:
                await get_storage().save(object_name, data,
                                         p["mime"] or "application/octet-stream")
            except Exception as e:  # noqa: BLE001  存储后端报错只多这一个文件落不下去
                log.exception(f"上传落存储失败 kb={kb.kb_id} file={p['name']}")
                p["error"] = f"保存失败：{str(e)[:150]}"
                return
            p["objectName"] = object_name
            p["size"] = len(data)

    @staticmethod
    async def _drop_object(object_name: str) -> None:
        """尽力删掉一个已经没有文档行指向它的原件；删不掉只记日志，不回头阻断上传。"""
        try:
            await get_storage().delete(object_name)
        except Exception as e:  # noqa: BLE001
            log.warning(f"孤儿原件清理失败 {object_name}: {e}")

    @staticmethod
    async def _enqueue_one(sem: "asyncio.Semaphore", kb: KnowledgeBase, p: dict) -> None:
        """投递解析任务（``enqueue_document`` 自带写 task_id 的状态流转）。

        它内部要自己开一段短会话，所以并发度受连接池而不是受本批大小限制；
        入队失败的意外（Redis 断连等）也按「排队失败」给回执，不拉下整批。
        """
        async with sem:
            try:
                p["taskId"] = await RagTaskService.enqueue_document(
                    int(p["docId"]), kb.kb_type)
            except Exception as e:  # noqa: BLE001
                # 文件已在存储里、行也建好了，只是没排上队：标失败让用户在列表里点重试
                await RagDocService._mark_enqueue_failed(int(p["docId"]), e)
                p["queueError"] = f"排队失败：{str(e)[:150]}"

    @staticmethod
    def _reject(file_name: str, message: str) -> dict:
        """被拒文件的回执（与成功项同结构，页面按同一张表渲染）。"""
        return {"docId": 0, "fileName": (file_name or "")[:FILE_NAME_MAX],
                "taskId": None, "status": None, "message": message}

    @staticmethod
    async def _mark_enqueue_failed(doc_id: int, error: Exception) -> None:
        """入队失败时把原因落到文档行（列表里能看到「排队失败」而不是永远显示排队中）。"""
        await RagTaskService.fail(doc_id, error)

    # ==================== 四、文档动作 ====================

    @staticmethod
    async def rename(login_user: dict, doc_id: int, title: str) -> bool:
        """改标题（只动展示名，不动原件与切片，也不推解析版本）。"""
        text = (title or "").strip()
        if not text:
            raise ValueError("文档标题不能为空")
        await RagDocService.require(login_user, doc_id, ACTION_EDIT)
        async with mysql_client.get_session() as session:
            await session.execute(update(Document).where(
                Document.doc_id == int(doc_id)).values(
                    doc_name=text[:TITLE_MAX],
                    update_by=int(login_user.get("user_id") or 0)))
            await session.commit()
        return True

    @staticmethod
    async def retry(login_user: dict, doc_ids: Sequence[int], *,
                    sidecar: str = RC.RAG_SIDECAR_AUTO) -> dict:
        """失败重试 / 手动重跑（换新任务号，逐项回执）。

        重试一律带 ``retry=True``：arq 会保留已完成任务的结果一段时间，沿用旧 job_id
        会被判成重复投递静默跳过，页面表现就是「点了重试但状态一直不动」。
        """
        ids = [int(d) for d in doc_ids if d]
        if not ids:
            raise ValueError("没有指定文档")
        items: list[dict] = []
        for doc_id in ids:
            try:
                doc = await RagDocService.require(login_user, doc_id, ACTION_REPARSE)
                kb = await RagDocService._kb_of(doc.kb_id)
                task_id = await RagTaskService.enqueue_document(
                    doc_id, kb.kb_type, retry=True, sidecar=sidecar)
                items.append({"docId": doc_id, "fileName": doc.doc_name, "taskId": task_id,
                              "status": RC.DOC_STATUS_PENDING, "message": None})
            except Exception as e:  # noqa: BLE001  一篇重跑不动其它篇
                items.append({"docId": doc_id, "fileName": None, "taskId": None,
                              "status": None, "message": str(e)[:200]})
        return RagDocService._action_result(items)

    @staticmethod
    async def build_vectors(login_user: dict, kb_id: int, req: BuildVectorReq) -> dict:
        """构建向量（需求 10，原名「重新分块」）：先删旧向量，再重新分块与向量化。

        两件事必须按这个顺序：
        1. **doc_ids 必填**。这个按钮就在上传右边，不给「不传=整库」的兜底，
           误点一次的代价是整库重跑；没勾选就让前端提示「请选择目标文档」。
        2. **先清 ES 再重跑**。索引 _id 由 chunk_index 决定，重新分块后块数几乎必然变化，
           只覆盖不删的话多出来的旧块会永远留在索引里被检索命中（旧切片还能命中、
           新切片查不到，这类不一致比直接报错难查一个量级）。

        给了 chunk_config/chunk_size 就先把配置写进库里（这一步卡 edit 并推版本号），
        没给就是「配置没变、重跑一次」；``reparse=True`` 时连解析一起重跑。
        """
        ids = [int(d) for d in (req.doc_ids or []) if d]
        if not ids:
            raise ValueError("请选择目标文档")
        async with mysql_client.get_session() as session:
            kb = await RagDocService._require_kb(login_user, kb_id, ACTION_EDIT, session)
            await KnowledgeBaseService.apply_chunk_override(
                login_user, kb_id, chunk_config=req.chunk_config,
                chunk_size=req.chunk_size, chunk_overlap=req.chunk_overlap, session=session)
            rows = (await session.execute(
                select(Document.doc_id, Document.doc_name).where(
                    Document.kb_id == int(kb_id), Document.doc_id.in_(ids),
                    Document.is_deleted == 0))).all()
        if not rows:
            raise ValueError("所选文档都不属于该知识库或已被删除")
        targets = [int(r[0]) for r in rows]

        # 旧向量清零：ES 删失败就中止（带着一半旧向量重跑比不跑更难解释）
        purged = 0
        for one in targets:
            try:
                purged += await es_client.delete_by_doc(one, kb_id)
            except Exception as e:  # noqa: BLE001
                log.warning(f"构建向量前清理旧索引失败 doc={one}: {e}")
                raise ValueError(f"清理旧向量数据失败，已中止构建：{str(e)[:150]}")
        async with mysql_client.get_session() as session:
            await session.execute(update(Document).where(
                Document.doc_id.in_(targets)).values(vectorized=0))
            await session.execute(update(DocumentChunk).where(
                DocumentChunk.doc_id.in_(targets)).values(vectorized=0))
            await session.commit()

        sidecar = RC.RAG_SIDECAR_REPARSE if req.reparse else RC.RAG_SIDECAR_REUSE
        result = await RagDocService.retry(login_user, targets, sidecar=sidecar)
        result["sidecar"] = sidecar
        result["purged"] = purged
        result["kbId"] = int(kb.kb_id)
        log.info("构建向量已投递: kb={} 文档={} 清理旧向量={} 策略={}",
                 kb_id, len(targets), purged, sidecar)
        return result

    @staticmethod
    async def remove(login_user: dict, kb_id: int, doc_ids: Sequence[int]) -> dict:
        """删除文档：MySQL 软删 + 投 rag 清理任务擦 ES/Neo4j/存储对象。

        底层清理放异步：一个库里可能几万条切片，同步 delete_by_query 会把请求挂到超时。
        业务行只软删（保留归属与文件名便于审计），存储侧的对象由 purge 任务按行反推删除。
        """
        ids = [int(d) for d in doc_ids if d]
        if not ids:
            raise ValueError("没有指定文档")
        async with mysql_client.get_session() as session:
            await RagDocService._require_kb(login_user, kb_id, ACTION_VIEW, session)
            rows = (await session.execute(select(Document).where(
                Document.doc_id.in_(ids), Document.kb_id == int(kb_id),
                Document.is_deleted == 0))).scalars().all()
            targets: list[int] = []
            for doc in rows:
                try:
                    await ensure_action(login_user, RESOURCE_DOC, doc.doc_id, ACTION_DELETE,
                                        owner_id=doc.created_by, session=session,
                                        parent=(RESOURCE_KB, kb_id))
                except Exception:  # noqa: BLE001  没权限的那些文档跳过并在回执里说明
                    continue
                targets.append(int(doc.doc_id))
            if not targets:
                raise ValueError("所选文档都没有可删除的权限")
            await session.execute(update(Document).where(
                Document.doc_id.in_(targets)).values(
                    is_deleted=1, status=RC.DOC_STATUS_PENDING,
                    update_by=int(login_user.get("user_id") or 0)))
            await session.commit()
            await KnowledgeBaseService.refresh_counters(int(kb_id), session=session)
        task_id = await RagTaskService.enqueue_purge(int(kb_id), targets)
        log.info("文档已删除: kb={} 数量={} purge_task={}", kb_id, len(targets), task_id)
        return {"kbId": int(kb_id), "accepted": len(targets),
                "rejected": len(ids) - len(targets), "taskId": task_id,
                "docIds": targets}

    # ==================== 五、切片管理 ====================

    @staticmethod
    async def chunks_page(login_user: dict, doc_id: int, req: ChunkPageReq) -> dict:
        """切片分页（正文在这里看，列表页不返回正文）。"""
        async with mysql_client.get_session() as session:
            doc = await RagDocService.require(login_user, doc_id, ACTION_VIEW, session=session)
            kb = await KnowledgeBaseService.load(session, doc.kb_id)
            conds = [DocumentChunk.doc_id == int(doc_id)]
            if req.chunk_type:
                if req.chunk_type not in RC.CHUNK_TYPES_ALL:
                    raise ValueError(f"未知的模态：{req.chunk_type}")
                conds.append(DocumentChunk.chunk_type == req.chunk_type)
            if req.keyword:
                conds.append(DocumentChunk.content.like(f"%{req.keyword}%"))
            if req.available is not None:
                conds.append(DocumentChunk.available == int(req.available))
            if req.kb_id:
                # 冗余过滤：前端跨文档核对权限时带上，与所属文档不一致就直接拒
                conds.append(DocumentChunk.kb_id == int(req.kb_id))
            total = (await session.execute(
                select(func.count()).select_from(DocumentChunk).where(*conds))).scalar() or 0
            rows = (await session.execute(
                select(DocumentChunk).where(*conds)
                .order_by(DocumentChunk.chunk_index.asc())
                .limit(req.size).offset((req.current - 1) * req.size))).scalars().all()
            items = [RagDocService._chunk_to_dict(r) for r in rows]
            await RagDocService._attach_chunk_media_urls(kb, items)
            return {"records": items, "total": total,
                    "current": req.current, "size": req.size}

    @staticmethod
    async def chunk_update(login_user: dict, chunk_id: int, req: ChunkUpdateReq) -> dict:
        """改切片正文或停用/放开（同步 ES，绝不让业务表与检索索引长期两份账）。

        可用性开关只改索引里的一个字段；改正文则连带重新向量化——旧文本的向量配新文本
        是最难查的问题（页面看到的和检索命中的不是同一份内容），所以这一步失败必须让
        用户看见，而不是悄悄留下 vectorized=1 的行。
        """
        if req.content is None and req.available is None:
            raise ValueError("没有要修改的内容")
        content: Optional[str] = None
        if req.content is not None:
            content = str(req.content).strip()
            if not content:
                raise ValueError("切片正文不能为空")
        message: Optional[str] = None
        es_id = ""
        doc_id = kb_id = org_id = 0
        embedding_model_id: Optional[int] = None
        available = False
        vectorized = 0
        async with mysql_client.get_session() as session:
            row = (await session.execute(select(DocumentChunk).where(
                DocumentChunk.chunk_id == int(chunk_id)))).scalar_one_or_none()
            if not row:
                raise ValueError("切片不存在")
            await RagDocService.require(login_user, row.doc_id, ACTION_CHUNK, session=session)
            kb = await KnowledgeBaseService.load(session, row.kb_id)
            if kb is None:
                raise ValueError("所属知识库不存在")
            es_id = es_doc_id(row.kb_id, row.doc_id, row.chunk_index)

            if req.available is not None:
                row.available = 1 if int(req.available) == 1 else 0
                await session.flush()
                if not await es_client.update_chunk(
                        es_id, {RC.ES_FIELD_AVAILABLE: bool(row.available)}):
                    message = "索引里没有这条切片（未向量化），可用性只作用于业务表"

            if content is not None:
                row.content = content
                row.embed_text = content
                row.token_count = len(content)
                row.vectorized = 0
            # commit 会 expire 全部 ORM 对象，异步会话里再读属性就得再发一次同步 IO（直接报错），
            # 所以把返回体和后续向量化要用的值一次性取成局部变量
            doc_id = int(row.doc_id)
            available = bool(row.available)
            vectorized = int(row.vectorized or 0)
            kb_id, org_id = int(kb.kb_id), int(kb.org_id or 0)
            embedding_model_id = kb.embedding_model_id
            await session.commit()

        if content is not None:
            # 正文换了，上面那次可用性同步算的还是旧向量的账，一切以重算结果为准
            vectorized, embed_message = await RagDocService._revectorize(
                int(chunk_id), es_id, kb_id, org_id, embedding_model_id)
            message = embed_message or message
        return {"chunkId": int(chunk_id), "docId": doc_id, "available": available,
                "vectorized": bool(vectorized), "message": message}

    @staticmethod
    async def chunk_remove(login_user: dict, chunk_id: int) -> dict:
        """删除切片（需求 7）：元数据 + 向量数据 + 图谱数据三份一起清。

        只删一份就是没删干净：行还在页面上就还能看到正文、ES 里还有就能被检索命中、
        Neo4j 里还有来源节点就会在图上挂着一个点不开原文的实体。
        卡的是文档的 chunk 动作（切片管理），与上面的编辑同源；删整篇才走 delete。

        先清行后清底层（底层失败可以在页面上重试，行先删了就再也算不出 es_id）；
        计数回写放在底层清理之后，让 chunk_count 与 ES 实际条数同一时刻对齐。
        """
        async with mysql_client.get_session() as session:
            row = (await session.execute(select(DocumentChunk).where(
                DocumentChunk.chunk_id == int(chunk_id)))).scalar_one_or_none()
            if not row:
                raise ValueError("切片不存在")
            doc = await RagDocService.require(login_user, row.doc_id, ACTION_CHUNK,
                                             session=session)
            es_id = es_doc_id(row.kb_id, row.doc_id, row.chunk_index)
            chunk_index = int(row.chunk_index or 0)
            kb_id, doc_id = int(row.kb_id), int(row.doc_id)
            doc_name = doc.doc_name
            await session.execute(delete(DocumentChunk).where(
                DocumentChunk.chunk_id == int(chunk_id)))
            await session.commit()

        # ES 删失败只降级成一句提示：行已经删了，回滚只会让页面与索引多一份对不上的账
        note: Optional[str] = None
        try:
            es_deleted = await es_client.delete_by_ids([es_id])
        except Exception as e:  # noqa: BLE001
            log.warning(f"切片向量删除失败 chunk={chunk_id} es_id={es_id}: {e}")
            es_deleted = 0
            note = f"切片已从列表删除，但检索索引里的向量没删掉，请重试一次：{str(e)[:120]}"
        # 图谱侧只在 Neo4j 可用时清（没开图谱的库本来就没写过节点，报错也不能算删切片失败）
        graph: dict[str, int] = {"deleted_nodes": 0, "deleted_rels": 0}
        try:
            graph = await kg_store.delete_by_chunk(kb_id, doc_id, es_id)
        except Exception as e:  # noqa: BLE001  底层图数据残留可以重跑图谱，不回滚已删的切片
            log.warning(f"切片图谱清理失败 chunk={chunk_id} es_id={es_id}: {e}")
            note = note or f"切片与向量已删，图谱侧残留请重跑构建图谱：{str(e)[:120]}"

        left = 0
        async with mysql_client.get_session() as session:
            left = (await session.execute(
                select(func.count()).select_from(DocumentChunk).where(
                    DocumentChunk.doc_id == doc_id))).scalar() or 0
            await session.execute(update(Document).where(
                Document.doc_id == doc_id).values(chunk_count=int(left)))
            await session.commit()
        await KnowledgeBaseService.refresh_counters(kb_id)
        log.info("切片已删除: kb={} doc={} index={} es={} graph={}",
                 kb_id, doc_id, chunk_index, es_deleted, graph)
        return {"chunkId": int(chunk_id), "docId": doc_id, "kbId": kb_id,
                "esDeleted": int(es_deleted), "graphDeletedNodes": int(graph.get("deleted_nodes") or 0),
                "graphDeletedRels": int(graph.get("deleted_rels") or 0),
                "chunkCount": int(left), "docName": doc_name, "message": note}

    @staticmethod
    async def _revectorize(chunk_id: int, es_id: str, kb_id: int, org_id: int,
                           embedding_model_id: Optional[int]) -> tuple[int, Optional[str]]:
        """单片重新向量化并覆盖写 ES 的 content+embedding，返回 (vectorized, 提示)。

        自己开一段短会话：向量模型一次往返可能几十秒，摊在主会话里会让这条切片的行一直
        被锁着，同一页上的另一次编辑只能干等。失败时改用 Core 语句把 vectorized 落成 0——
        刚回滚过的 ORM 对象已经过期，再去改它的属性会触发一次同步刷新。
        """
        async with mysql_client.get_session() as session:
            row = (await session.execute(select(DocumentChunk).where(
                DocumentChunk.chunk_id == int(chunk_id)))).scalar_one_or_none()
            if not row:
                return 0, "切片不存在，未做向量化"
            content = row.content or ""
            full_doc = RagDocService._chunk_es_doc(row, es_id, kb_id, org_id)
            try:
                cfg = await RagModelService.require_config(embedding_model_id, "向量模型")
                vectors = await RagModelService.embed_texts(cfg, [row.embed_text or content])
                vector = [float(x) for x in list(vectors[0])] if vectors else []
                if not vector or not any(x > 0.0 or x < 0.0 for x in vector[:8]):
                    raise ValueError("向量模型返回了空向量")
                if not await es_client.update_chunk(
                        es_id, {RC.ES_FIELD_CONTENT: content, RC.ES_FIELD_EMBED: vector}):
                    # 索引里没有这条（历史上被跳过，或整篇重跑过）：整条重写，_id 幂等
                    await es_client.put_chunks(
                        [{**full_doc, RC.ES_FIELD_EMBED: vector}], refresh="wait_for")
                row.vectorized = 1
                await session.commit()
                return 1, None
            except Exception as e:  # noqa: BLE001  正文已经落库，向量这一步失败要说清楚
                await session.rollback()
                await session.execute(update(DocumentChunk).where(
                    DocumentChunk.chunk_id == int(chunk_id)).values(vectorized=0))
                await session.commit()
                log.warning(f"单片重新向量化失败 chunk={chunk_id}: {e}")
                return 0, f"正文已保存，但重新向量化失败（检索仍按旧语义命中）：{str(e)[:150]}"

    @staticmethod
    def _chunk_es_doc(row: DocumentChunk, es_id: str, kb_id: int, org_id: int) -> dict:
        """切片行 → ES 文档（不含 embedding）：索引里没这条时整条重写用。

        键集合必须落在 index_mapping 的 strict 白名单内（多一个键整批写入就报错），
        所以这份列顺序与 parse_service._index_rows 保持一致。
        """
        return {
            "_id": es_id,
            RC.ES_FIELD_ORG: int(org_id),
            RC.ES_FIELD_KB: int(kb_id),
            RC.ES_FIELD_DOC: int(row.doc_id),
            RC.ES_FIELD_CHUNK: int(row.chunk_id),
            RC.ES_FIELD_INDEX: int(row.chunk_index),
            RC.ES_FIELD_TYPE: row.chunk_type or RC.CHUNK_TYPE_TEXT,
            RC.ES_FIELD_CONTENT: row.content or "",
            RC.ES_FIELD_MEDIA_URL: row.media_url or None,
            RC.ES_FIELD_TITLE_PATH: row.title_path or None,
            RC.ES_FIELD_PAGE: int(row.page_num or 0),
            RC.ES_FIELD_SHEET: row.sheet_name or None,
            RC.ES_FIELD_AVAILABLE: bool(row.available),
            RC.ES_FIELD_DELETED: 0,
            RC.ES_FIELD_CREATE_TIME: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            RC.ES_FIELD_SOURCE: to_json(row.extra or {}),
        }

    # ==================== 序列化 ====================

    @staticmethod
    def _to_dict(row: Document) -> dict:
        """ORM 行 → 前端契约字典。"""
        resp = DocumentResp(
            id=row.doc_id, kb_id=row.kb_id, title=row.doc_name, file_name=row.file_name,
            file_ext=row.file_ext, file_size=int(row.file_size or 0),
            content_type=row.content_type, media_type=row.media_type,
            parser_engine=row.parser_engine, parse_version=int(row.parse_version or 0),
            status=row.status,
            status_label=RC.DOC_STATUS_LABELS.get(row.status, row.status),
            vectorized=bool(row.vectorized), graph_state=int(row.graph_state or 0),
            graph_state_label=RC.KG_STATE_LABELS.get(int(row.graph_state or 0), None),
            chunk_count=int(row.chunk_count or 0), page_count=int(row.page_count or 0),
            media_duration=int(row.media_duration or 0), media_summary=row.media_summary,
            task_id=row.task_id, error_msg=row.error_msg,
            created_by=int(row.created_by or 0),
            create_time=_fmt(row.create_time), update_time=_fmt(row.update_time))
        return resp.dump()

    @staticmethod
    def _chunk_to_dict(row: DocumentChunk) -> dict:
        resp = ChunkResp(
            id=row.chunk_id, kb_id=row.kb_id, doc_id=row.doc_id,
            chunk_index=int(row.chunk_index or 0),
            chunk_type=row.chunk_type or RC.CHUNK_TYPE_TEXT,
            chunk_type_label=RC.CHUNK_TYPE_LABELS.get(row.chunk_type or "", row.chunk_type),
            content=row.content or "", embed_text=row.embed_text,
            title_path=row.title_path, sheet_name=row.sheet_name,
            page_num=int(row.page_num or 0), block_id=row.block_id,
            media_url=row.media_url, media_type=row.media_type,
            token_count=int(row.token_count or 0), available=bool(row.available),
            vectorized=bool(row.vectorized), extra=row.extra,
            create_time=_fmt(row.create_time))
        return resp.dump()

    @staticmethod
    async def _attach_media_urls(kb: Optional[KnowledgeBase], items: list[dict],
                                 paths: dict[int, str]) -> None:
        """详情行补一条**原件**的可访问地址（本地后端没配 public_base 就留空）。

        只在 detail() 用：列表已改成表格、不自动加载多媒体，预览走 original_preview。
        doc 型库里内嵌图片的地址在切片层，由 _attach_chunk_media_urls 处理。
        """
        if kb is None or not items:
            return
        for item in items:
            item["mediaUrl"] = await _sign(paths.get(int(item.get("id") or 0), ""))

    @staticmethod
    async def _attach_chunk_media_urls(kb: Optional[KnowledgeBase], items: list[dict]) -> None:
        """切片列表的图片句柄换成可访问地址（doc 型库的内嵌图也走这一条）。"""
        if kb is None or not items:
            return
        for item in items:
            name = item.get("mediaUrl") or ""
            if name:
                item["mediaUrl"] = await _sign(name)

    @staticmethod
    async def _kb_of(kb_id: int) -> KnowledgeBase:
        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.load(session, kb_id)
            if kb is None:
                raise ValueError("知识库不存在")
            return kb

    @staticmethod
    def _action_result(items: list[dict]) -> dict:
        """批量动作的统一回执。"""
        ok = sum(1 for i in items if i.get("taskId"))
        return {"accepted": ok, "rejected": len(items) - ok, "items": items}


async def _sign(name: str) -> Optional[str]:
    """对象名 → 匿名可访问地址；拿不到就返回 None（页面显示占位，不整体报错）。

    已经是完整 URL 的原样透出：历史数据里有过直接落库的签名地址，
    把它当对象名再签一次会被 check_name 拒掉（含冒号），页面就成了一片空白。
    """
    if not name:
        return None
    if "://" in name:
        return name
    try:
        url, _expires = await get_storage().public_url(name)
        return url
    except Exception as e:  # noqa: BLE001
        log.debug(f"签名地址生成失败 {name}: {e}")
        return None


def _fmt(value) -> Optional[str]:
    """DATETIME → 前端展示串（与 kb_service 同口径）。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


__all__ = ["RagDocService"]

