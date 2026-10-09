# -*- coding: utf-8 -*-
"""知识图谱：文档级抽取任务（worker 侧）与图谱读写口（API 侧）。

两侧分工
--------
* ``run_build`` 只在 graph worker 里被调用（graphflow 流水线的 arq 任务 ``build_document_graph``），一篇文档的
  **全部**可用切片按批送大模型抽取，批与批的结果在 Python 侧合并后一次写入；
  库开关自动触发与页面手动按钮投的是同一个任务，跑出来不会两种结果。
* ``build/query/statistics/search/sources`` 是 API 侧，只做「算白名单 + 转契约」，
  一句 Cypher 都不写——图谱语句全部收在 common_neo4j.kg_store 里。

六条硬口径
----------
1. **kb_id 白名单先算后查**（SPEC §2、§10.3）：Neo4j 是裸查存储，任何一次读都先把
   「当前用户可见 + doc 型」的库 id 集合算出来带下去；只按实体名查会让别人的库里
   同名实体的关系混进图里。
2. **图谱是可失败的增强**：抽取失败只把 ``graph_state`` 打成 FAILED、原因写进进度，
   文档状态仍是 PROCESSED。一篇能被正常检索的文档不该因为图谱被判成失败。
   反过来，**一个实体都没抽到也不算失败**：目录、页眉、纯表格这类内容本就没有可成图的
   事实，照样打 DONE，只在完成文案里说清图谱是空的（算失败的是模型调用打不通、
   没有可用切片、没配抽取模型这三类）。
3. **重跑不叠加**：``upsert_graph(replace=True)`` 先按文档清掉上一轮子图；已构建的文档
   只有 ``force=True`` 才重抽——一次重抽就是 N 轮大模型调用（按切片分批），不该走默认路径。
4. **端点类型必须与实体一致**：实体 MERGE 键里带了 type，关系端点给的 type 与实体行
   不一致就会在图上裂成两个同名词节点，而裂开之后再也合不回来，所以 relations 的
   ``head_type/tail_type`` 一律从实体表回填。
5. **溯源回得到切片**：实体挂的 ``chunk_ids`` 是 ES 的 ``_id``，由提示词里 ``[切片N]``
   的编号（= chunk_index）换回来；模型编出的不存在的编号直接丢掉，不留指向空切片的桩。
6. **断点只为省钱**：每抽完一批就把归一化结果落一次断点（Redis），下次构建只补没抽成的批。
   它是可丢旁路：读不到、签名不对、写失败都只是退回多抽一批，不参与「有没有图谱」的判定；
   而写入图谱成功即整份删掉，不让一份旧结果在下一次全量重抽里假装成新抽的。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, Sequence

from sqlalchemy import select, update

from common.common_constants import rag_constant as RC
from common.common_es.index_mapping import es_doc_id
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_neo4j import kg_store, neo4j_client
from common.common_permission.resource_guard import (
    ACTION_GRAPH, ACTION_VIEW, ensure_action, maybe_session,
)
from service.service_rag.models.kb_entity import Document, DocumentChunk, KnowledgeBase
from service.service_rag.schemas.rag_schema import (
    GraphBuildReq, GraphEdgeResp, GraphNodeResp, GraphQueryReq, GraphResp,
)
from service.service_rag.services import kg_ckpt
from service.service_rag.services import kg_vector
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.parse_service import load_pair
from service.service_rag.services.kb_service import KnowledgeBaseService
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.task_service import RagTaskService

# 资源码（文档层自己卡动作，知识库层走 KnowledgeBaseService.require）
RESOURCE_DOC = "document"
RESOURCE_KB = "knowledge_base"


class RagGraphService:
    """图谱构建与查询的管理层（全静态方法，router 与 worker 直接 await）"""

    # ==================== 一、worker 侧：单文档抽取 ====================

    @staticmethod
    async def run_build(doc_id: int, options: Optional[dict] = None) -> dict:
        """一篇文档的图谱构建：读切片 → 分批送大模型抽实体关系 → 合并 → 落 Neo4j。

        :param options: 任务选项（与入队时的 args 同源）：``{"force": true}`` 才重抽
            已构建的文档（手动按钮上的「重新构建」）；``{"ckpt": "resume"|"restart"}``
            是断点策略，缺省 resume——上一轮失败的文档只补没抽成的批，不重烧已经抽过的模型调用
        :return: {"doc_id","entities","relations","chunks"}（权威状态在 MySQL 的 graph_state）
        """
        force = bool((options or {}).get("force"))
        ckpt_mode = _ckpt_mode(options)
        token = await RagTaskService.acquire_doc(doc_id)
        if not token:
            # 同一文档还在解析/重跑：图谱挂到下一轮（切片表此刻可能是旧的）
            log.warning(f"文档已有任务在跑，跳过图谱构建: doc_id={doc_id}")
            return {"doc_id": int(doc_id), "skipped": True}

        kb_id = 0
        # 给失败文案用的批数口径：try 里的局部变量在 except 里看不到，所以放在外面由循环自己更新
        progressed = {"total": 0, "done": 0, "reused": 0}
        try:
            doc, kb = await load_pair(doc_id)
            kb_id = int(kb.kb_id)
            _check_buildable(doc, kb)
            log.info("开始图谱构建: doc={} kb={} name={} force={}",
                     doc_id, kb_id, doc.doc_name, force)

            if int(doc.graph_state or 0) == RC.KG_STATE_DONE and not force:
                log.info(f"图谱已构建，未要求重抽: doc={doc_id}")
                await RagTaskService.set_progress(
                    doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_done",
                    message="图谱已构建（勾选文档重新构建才会重抽）")
                return {"doc_id": int(doc_id), "skipped": True, "reason": "已构建"}

            await RagTaskService.set_progress(
                doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_load", reset=True,
                total=0, done=0, message="读取切片准备抽取实体")
            rows = await _load_chunks(doc_id)
            if not rows:
                raise ValueError("该文档没有可用切片（先完成向量化，"
                                 "或在切片管理里启用若干切片后重试）")
            log.info("阶段[读切片]完成: doc={} 可用切片={}", doc_id, len(rows))
            es_ids = {int(r["chunk_index"]): es_doc_id(kb_id, doc_id, int(r["chunk_index"]))
                      for r in rows}

            # 抽取模型与媒体理解模型分列（图谱抽取在「解析配置」页单独选）；
            # 存量库 extract_model_id=0 时回退 chat_model_id，不让它们突然抽不出实体
            extract_id = int(kb.extract_model_id or 0) or int(kb.chat_model_id or 0)
            if not extract_id:
                raise ValueError("知识库没有配置图谱抽取模型，无法抽取实体（请在知识库的解析配置里选）")
            cfg = await RagModelService.require_config(extract_id, "图谱抽取模型")
            # 不封顶总片数，只拆成每次调用若干片：单条 prompt 装下几百片会直接超模型
            # 上下文，那一类失败是「整篇抽不出来」，比多跑几次调用严重得多
            batch = max(1, settings.graph_extract_batch_chunks())
            batches = [rows[i:i + batch] for i in range(0, len(rows), batch)]
            progressed["total"] = len(batches)
            # 断点能不能吃由「批次划分签名」决定：抽取模型 + 每批片数 + 切片编号序列。
            #   批是按位置切的，三者任一变了「第 3 批」就是另一堆切片，只能整份作废重抽
            model_tag = (str(getattr(cfg, "model_name", "") or "").strip()
                         or str(getattr(cfg, "name", "") or "").strip() or str(extract_id))
            want_sig = kg_ckpt.sig(model=model_tag, batch_chunks=batch,
                                   indexes=[int(r["chunk_index"]) for r in rows])
            cached = await _load_ckpt(doc_id, ckpt_mode, want_sig)
            # hash 里除了 meta 签名，其余一个字段就是一批可复用结果（指纹对不对到循环里逐批验）
            reusable = max(0, len(cached) - 1)
            log.info("阶段[抽取]开始: doc={} 切片={} 分{}批（每批≤{}）断点可复用={}批",
                     doc_id, len(rows), len(batches), batch, reusable)
            await RagTaskService.set_progress(
                doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_extract", total=len(rows), done=0,
                message=f"抽取实体与关系（{len(rows)} 个切片，分 {len(batches)} 批）"
                        + (f"，断点里已有 {reusable} 批可直接复用" if reusable else ""))
            drawn: list[tuple[list[dict], list[dict], list[str]]] = []
            for no, part in enumerate(batches, start=1):
                prompt = settings.build_extract_prompt(
                    [(r["chunk_index"], str(r["content"] or "")) for r in part])
                stamp = kg_ckpt.prompt_stamp(prompt)
                # 上一轮抽成功且内容没改的批：直接吃断点，不再调模型（断点的全部意义在这一行）
                hit = kg_ckpt.unpack(cached.get(kg_ckpt.batch_field(no)) or "", stamp=stamp)
                if hit is not None:
                    drawn.append(hit)
                    progressed["reused"] += 1
                    progressed["done"] = no
                    log.info("阶段[抽取]复用断点: doc={} 第{}/{}批 实体={} 关系={}",
                             doc_id, no, len(batches), len(hit[0]), len(hit[1]))
                    await RagTaskService.set_progress(
                        doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_extract",
                        total=len(rows), done=min(len(rows), no * batch),
                        message=_extract_message(no, len(batches), progressed["reused"]))
                    continue
                payload = await _extract_batch(cfg, prompt, doc_id=doc_id, no=no,
                                               total=len(batches))
                # 一批没抽到实体不是失败：只记一句说明，整篇都为空也按成功收口（见下面的完成文案）
                part_result = _normalize(payload, es_ids)
                drawn.append(part_result)
                progressed["done"] = no
                if not await _save_ckpt(doc_id, no, part_result, stamp=stamp, sig=want_sig):
                    log.warning(f"第 {no} 批结果未存进断点（过大或 Redis 写失败），下次只能重抽这一批: "
                                f"doc={doc_id}")
                log.info("阶段[抽取]进展: doc={} 第{}/{}批 切片={} 实体={} 关系={}",
                         doc_id, no, len(batches), len(part),
                         len(part_result[0]), len(part_result[1]))
                await RagTaskService.set_progress(
                    doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_extract",
                    total=len(rows), done=min(len(rows), no * batch),
                    message=_extract_message(no, len(batches), progressed["reused"]))

            entities, relations, notes = _merge_batches(drawn)
            if progressed["reused"]:
                # 续抽要看得见：完成文案里说清本次省掉了多少批模型调用
                notes = notes + [
                    f"续抽复用断点 {progressed['reused']}/{len(batches)} 批"]
            log.info("阶段[抽取]完成: doc={} 合并后实体={} 关系={} 降级说明={}",
                     doc_id, len(entities), len(relations), len(notes))

            await RagTaskService.set_progress(
                doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, stage="graph_write", total=len(rows), done=len(rows),
                message=f"写入图谱（{len(entities)} 个实体 / {len(relations)} 条关系）")
            log.info("阶段[写图]开始: doc={} 实体={} 关系={}", doc_id, len(entities), len(relations))
            await kg_store.ensure_document(kb_id, doc_id, org_id=int(kb.org_id or 0),
                                           title=doc.doc_name or "")
            counts = await kg_store.upsert_graph(
                kb_id, doc_id, entities, relations,
                chunks=[{"es_id": es_ids[int(r["chunk_index"])],
                         "chunk_index": int(r["chunk_index"]),
                         "content": str(r["content"] or ""),
                         "title_path": str(r["title_path"] or "")} for r in rows],
                org_id=int(kb.org_id or 0), replace=True)

            # 需求 9a：图谱真身落 Neo4j 后同步它的向量投影（rag_kg_vector）。
            # 按整库算差集而不是只送本篇：实体是库内同名同类合并的，本篇会改写到
            # 别的文档提过的实体摘要。投影失败不推翻已建好的图（与「图谱是可失败的
            # 增强」同口径），但必须写进页面说明：检索页开了图谱增强却发现一路空，
            # 看不到原因比建图失败更难查。
            try:
                embed_cfg = await RagModelService.get_config(int(kb.embedding_model_id or 0))
                vec = await kg_vector.sync_kb_vectors(
                    kb_id, embed_model_id=int(kb.embedding_model_id or 0),
                    org_id=int(kb.org_id or 0), embed_config=embed_cfg)
                if vec.get("truncated"):
                    notes = notes + [f"实体/关系向量仅投影前 {RC.KG_VECTOR_SCAN_LIMIT} 条"]
            except Exception as ve:  # noqa: BLE001  投影失败只降级图谱增强检索
                log.exception(f"图谱向量投影失败（图谱已建好）: doc={doc_id} kb={kb_id}")
                notes = notes + [f"实体/关系向量未更新（{str(ve)[:120]}），"
                                f"图谱增强检索需重建后使用"]

            await _save_state(doc_id, RC.KG_STATE_DONE)
            # 结果已落 Neo4j，断点再留着没有下一步可用（下一次要么全量重抽、要么内容已变），
            #   当场清掉才不会出现「旧批次的抽取结果在新切片集上假装是新抽的」
            await RagTaskService.kg_ckpt_drop(doc_id)
            if not entities:
                # 没抽到实体是正常结果，不是异常：状态照样 DONE，只把「图谱是空的」说清楚。
                # 上一轮的旧子图在上面已由 upsert_graph(replace=True) 清掉，不会留下该消失的实体
                message = (f"图谱构建完成：{len(rows)} 个切片没有抽取出实体，图谱为空"
                           f"（内容可能以目录、页眉或表格为主，没有可成图的事实）")
            else:
                message = f"图谱构建完成：{counts.get('entities', 0)} 个实体、" \
                          f"{counts.get('relations', 0)} 条关系"
                if notes:
                    message += f"（{('；'.join(notes))[:200]}）"
            await RagTaskService.set_progress(doc_id, scope=RC.PROGRESS_SCOPE_GRAPH,
                                              stage="graph_done",
                                              total=len(rows), done=len(rows),
                                              message=message[:500])
            log.info("图谱构建完成: doc={} kb={} 实体={} 关系={} 切片={}",
                     doc_id, kb_id, counts.get("entities"), counts.get("relations"),
                     len(rows))
            return {"doc_id": int(doc_id), "kb_id": kb_id,
                    "entities": int(counts.get("entities") or 0),
                    "relations": int(counts.get("relations") or 0),
                    "chunks": len(rows), "notes": notes}
        except Exception as e:  # noqa: BLE001  原因写进进度，文档状态一律不动
            log.exception(f"图谱构建失败: doc_id={doc_id}")
            await _save_state(doc_id, RC.KG_STATE_FAILED)
            # 断点留在失败前一批：下次点构建只补剩下的批，所以文案必须说清从第几批接着抽
            await RagTaskService.set_progress(
                doc_id, scope=RC.PROGRESS_SCOPE_GRAPH,
                message=_fail_message(e, progressed))
            raise
        finally:
            await RagTaskService.release_doc(doc_id, token)

    # ==================== 二、API 侧：手动触发构建 ====================

    @staticmethod
    async def build(login_user: dict, kb_id: int, req: GraphBuildReq) -> dict:
        """构建图谱（需求 10，页面按钮）：先清掉这些文档上一轮的图数据，再重新抽取。

        四条口径：
        - **doc_ids 必填**：整库重抽等于按篇数烧一遍大模型调用，误点一次的代价太大，
          没勾选就直回「请选择目标文档」；
        - **force 恒为真**：按钮的语义就是「重新构建」，旧子图由
          ``upsert_graph(replace=True)`` 先清后写，重跑不在图上叠加历史抽取结果；
        - **默认续抽**（``req.resume``，页面上是一个勾选项）：失败重跑最常见的形状就是
          「只差最后几批」，吃断点只补没抽成的批；取消勾选等于全量重抽（换了模型、
          改了抽取提示词这种「旧结果不算数」的场景）；
        - 库开关只决定「解析完成后要不要自动抽一遍」，手动按钮不该被它关掉：用户明确
          点了构建就是明确要图谱数据，这时候还回一句「该库未开启图谱」属于把开关理解错了。
        """
        ids = [int(d) for d in (req.doc_ids or []) if d]
        if not ids:
            raise ValueError("请选择目标文档")
        if not neo4j_client.ready:
            raise ValueError("图谱服务（Neo4j）未初始化，无法构建知识图谱")
        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.require(login_user, kb_id, ACTION_VIEW,
                                                    session=session)
            if kb.kb_type != RC.KB_TYPE_DOC:
                raise ValueError("只有文档问答型知识库支持知识图谱")
            # 抽取模型与媒体理解模型分列，存量库回退 chat_model_id（与 worker 侧同口径）
            if not int(kb.extract_model_id or 0) and not int(kb.chat_model_id or 0):
                raise ValueError("知识库没有配置图谱抽取模型，无法抽取实体"
                                 "（请在知识库的解析配置里选择抽取模型）")
            kb_type = str(kb.kb_type)       # 循环里会提交，ORM 行随后就被 expire，先把要用的列取出来
            conds = [Document.kb_id == int(kb_id), Document.doc_id.in_(ids),
                     Document.is_deleted == 0, Document.status == RC.DOC_STATUS_PROCESSED]
            # 只取三个列、拿元组：后面的入队会多次 commit，逐行读 ORM 对象会碰到过期属性
            rows = (await session.execute(
                select(Document.doc_id, Document.created_by, Document.graph_state)
                .where(*conds))).all()
            if not rows:
                raise ValueError("所选文档都不属于该知识库、已被删除或还没解析完成")

            items: list[dict] = []
            # 断点策略由页面勾选项决定（默认续抽）：抽取模型/批次划分/切片内容都没变时，
            #   上一轮抽成功的批不必再烧一次大模型调用
            ckpt = RC.RAG_KG_CKPT_RESUME if req.resume else RC.RAG_KG_CKPT_RESTART
            for doc_id, created_by, graph_state in rows:
                doc_id = int(doc_id)
                try:
                    await ensure_action(login_user, RESOURCE_DOC, doc_id, ACTION_GRAPH,
                                        owner_id=int(created_by or 0), session=session,
                                        parent=(RESOURCE_KB, kb_id))
                except Exception as e:  # noqa: BLE001  没权限的文档跳过，回执里说明
                    items.append({"docId": doc_id, "taskId": None, "message": str(e)[:200]})
                    continue
                if int(graph_state or 0) == RC.KG_STATE_BUILDING:
                    items.append({"docId": doc_id, "taskId": None,
                                  "message": "该文档的图谱正在构建中"})
                    continue
                try:
                    # 手动触发一律换新任务号：同一 job_id 会被 arq 判成重复投递静默跳过
                    # force=True：重新构建就是重抽，旧子图由 worker 先清后写
                    task_id = await RagTaskService.enqueue_graph(
                        doc_id, kb_type=kb_type, retry=True,
                        force=True, ckpt=ckpt, session=session)
                except ValueError as e:
                    # 没排上队就不能停在「构建中」：否则用户盯着一个永远不会来的转圈
                    await _save_state(doc_id, RC.KG_STATE_NONE, session=session)
                    items.append({"docId": doc_id, "taskId": None,
                                  "message": f"排队失败：{str(e)[:150]}"})
                    continue
                # 进度条归零：上一轮的 100% 留着会让页面看起来像「一点就建完了」
                await RagTaskService.clear_progress(doc_id, scope=RC.PROGRESS_SCOPE_GRAPH)
                items.append({"docId": doc_id, "taskId": task_id, "message": None})

        accepted = [i for i in items if i.get("taskId")]
        log.info("图谱构建已投递: kb={} 成功={} 共={}", kb_id, len(accepted), len(items))
        return {"kbId": int(kb_id), "accepted": len(accepted),
                "skipped": len(items) - len(accepted), "items": items}

    # ==================== 三、API 侧：图谱读取 ====================

    @staticmethod
    async def kb_scope(login_user: dict, kb_ids: Optional[Sequence[int]] = None, *,
                       session=None) -> list[int]:
        """本次图谱操作允许的知识库白名单（SPEC §2：权限只在这层收口，底层裸查）。

        两层过滤：先取用户可见的库，再只留 doc 型——非 doc 型从来没写过图谱节点，
        把它们带进 Cypher 只会多几个空命中的 id，还会让「图谱为空」与「无权限」混成
        同一种表现。
        """
        wanted = {int(k) for k in (kb_ids or []) if k}
        async with maybe_session(session) as s:
            ids = await KnowledgeBaseService.visible_ids(login_user, session=s,
                                                          action=ACTION_VIEW)
            if wanted:
                ids = [k for k in ids if k in wanted]
            if not ids:
                return []
            rows = (await s.execute(
                select(KnowledgeBase.kb_id).where(
                    KnowledgeBase.kb_id.in_(ids),
                    KnowledgeBase.kb_type == RC.KB_TYPE_DOC))).scalars().all()
            return [int(r) for r in rows]

    @staticmethod
    async def query(login_user: dict, req: GraphQueryReq) -> dict:
        """子图查询（图谱可视化页）。

        起点条件按**叠加（AND）**算：关键词与实体类型同时生效，两个都填就必须同时命中。
        给了关键词却一个实体都没命中时**直接回空图并说明原因**，不退回库内度数最高：
        退回等于把关键词悄悄丢掉，页面上看着就是「只有实体类型生效」，用户无从判断
        到底是没搜到还是条件没生效。只有两个条件都没给时才用度数 top 兜底，
        让页面一进来有图可看。拿到起点后按 depth 逐跳展开，规模由 limit 硬卡住并如实回 truncated。
        """
        ids = await RagGraphService.kb_scope(login_user, req.kb_ids)
        limit = int(req.limit or 0) or settings.graph_viz_default_limit()
        if not ids or not neo4j_client.ready:
            return GraphResp(message=(
                "" if not ids else
                ("图谱服务（Neo4j）未初始化，本次没有子图可显示"
                 if not neo4j_client.ready else "所选范围内没有可看的文档型知识库"))).dump()

        kw = (req.keyword or "").strip()
        centers: list[str] = []
        if kw:
            # 实体类型在这里就带下去：与左栏「实体检索」同一个口径，两处筛出的起点应当一致
            centers = [str(e.get("name") or "").strip() for e in await kg_store.search_entities(
                ids, kw, limit=limit, entity_type=req.entity_type)]
            centers = [c for c in centers if c]
            if not centers:
                brief = kw[:30]
                if req.entity_type:
                    msg = (f"没有同时匹配关键词「{brief}」与实体类型「{req.entity_type}」的实体："
                           f"两个条件是叠加过滤，换个关键词或清空实体类型再查")
                else:
                    msg = (f"没有匹配关键词「{brief}」的实体：确认所选知识库已完成「构建图谱」，"
                           f"或换个更接近实体名的关键词")
                return GraphResp(message=msg).dump()
        elif req.doc_ids:
            centers = [str(e.get("name") or "") for e in await kg_store.entities_by_docs(
                ids, req.doc_ids, limit=limit)]
        raw = await kg_store.subgraph(ids, centers=centers, depth=req.depth,
                                      limit=limit, entity_type=req.entity_type)
        nodes = [_node_dto(r) for r in raw.get("nodes") or []]
        edges = [_edge_dto(r) for r in raw.get("edges") or []]
        return GraphResp(nodes=nodes, edges=edges, total_nodes=len(nodes),
                         total_edges=len(edges),
                         truncated=bool(raw.get("truncated"))).dump()

    @staticmethod
    async def statistics(login_user: dict,
                         kb_ids: Optional[Sequence[int]] = None) -> dict:
        """图谱规模概览（知识库详情页）；没图与连不上是两件事，分开回话。"""
        ids = await RagGraphService.kb_scope(login_user, kb_ids)
        if not ids:
            return {"kbIds": [], "ready": False, "enabled": False, "entityCount": 0,
                    "relationCount": 0, "chunkCount": 0, "documentCount": 0,
                    "types": [], "topEntities": []}
        if not neo4j_client.ready:
            return {"kbIds": ids, "ready": False, "enabled": False, "entityCount": 0,
                    "relationCount": 0, "chunkCount": 0, "documentCount": 0,
                    "types": [], "topEntities": [],
                    "message": "图谱服务（Neo4j）未初始化，以下统计不可用"}
        # top 是详情页「核心实体」榜单的长度：按节点上限的十分之一给，不再新开一个配置键
        top = max(5, settings.graph_viz_default_limit() // 10)
        raw = await kg_store.statistics(ids, top=top)
        return {"kbIds": ids, "ready": True, "enabled": bool(raw.get("enabled")),
                "entityCount": int(raw.get("entity_count") or 0),
                "relationCount": int(raw.get("relation_count") or 0),
                "chunkCount": int(raw.get("chunk_count") or 0),
                "documentCount": int(raw.get("document_count") or 0),
                "types": [{"type": t.get("type"), "count": int(t.get("count") or 0)}
                          for t in raw.get("types") or []],
                "topEntities": [{"name": t.get("name"), "type": t.get("type"),
                                 "kbId": int(t.get("kb_id") or 0),
                                 "degree": int(t.get("degree") or 0)}
                                for t in raw.get("top_entities") or []]}

    @staticmethod
    async def search(login_user: dict, keyword: str,
                     kb_ids: Optional[Sequence[int]] = None, *,
                     entity_type: Optional[str] = None,
                     limit: int = 0) -> list[dict]:
        """实体检索（图谱页搜索框、检索页的图谱增强入口）。"""
        ids = await RagGraphService.kb_scope(login_user, kb_ids)
        if not ids or not (keyword or "").strip() or not neo4j_client.ready:
            return []
        size = int(limit or 0) or settings.graph_viz_default_limit()
        rows = await kg_store.search_entities(ids, keyword, limit=size,
                                              entity_type=entity_type)
        return [_node_dto(r) for r in rows]

    @staticmethod
    async def sources(login_user: dict, name: str,
                      kb_ids: Optional[Sequence[int]] = None, *,
                      entity_type: Optional[str] = None,
                      limit: int = 50) -> list[dict]:
        """实体溯源：这个实体出自哪些切片（页面点节点回原文位置）。"""
        ids = await RagGraphService.kb_scope(login_user, kb_ids)
        if not ids or not (name or "").strip() or not neo4j_client.ready:
            return []
        rows = await kg_store.entity_sources(ids, name, entity_type=entity_type,
                                             limit=int(limit or 0) or 50)
        return [{"esId": r.get("es_id"), "kbId": int(r.get("kb_id") or 0),
                 "docId": int(r.get("doc_id") or 0),
                 "chunkIndex": int(r.get("chunk_index") or 0),
                 "titlePath": r.get("title_path") or "",
                 "content": r.get("content") or ""} for r in rows]


# =====================================================================================
# 内部：worker 侧
# =====================================================================================

def _check_buildable(doc: Document, kb: KnowledgeBase) -> None:
    """构建前置校验（不满足就是任务不该跑，抛 ValueError 让 arq 记一次失败）。

    ``neo4j_client.ready`` 也在这里判：图谱任务连不上库时把状态打成 FAILED 才有意义，
    否则文档一直显示「构建中」，用户在页面上等一个永远不来的结果。
    """
    if kb.kb_type != RC.KB_TYPE_DOC:
        raise ValueError("只有文档问答型知识库支持知识图谱")
    if doc.status != RC.DOC_STATUS_PROCESSED:
        raise ValueError(f"文档尚未解析完成（当前状态：{doc.status}），无法抽取图谱")
    if not int(kb.extract_model_id or 0) and not int(kb.chat_model_id or 0):
        raise ValueError("知识库没有配置图谱抽取模型，无法抽取实体")
    if not neo4j_client.ready:
        raise ValueError("图谱服务（Neo4j）未初始化，无法构建知识图谱")


def _ckpt_mode(options: Optional[dict]) -> str:
    """从任务选项里取断点策略（缺字段/认错值一律回到 resume）。

    与 parse_service 的 ``_sidecar_mode`` 同一口径：旧版本入队的任务只有一个 force，
    默认行为必须是「省钱的那一档」，而不是把一次正常构建打成失败。
    """
    mode = str((options or {}).get("ckpt") or "").strip().lower()
    return mode if mode in RC.RAG_KG_CKPT_MODES else RC.RAG_KG_CKPT_RESUME


async def _load_ckpt(doc_id: int, mode: str, want_sig: str) -> dict[str, str]:
    """取可复用的断点字段（拿不到就是空字典 = 从第一批重抽）。

    两种必须整份作废的情形：
    * ``restart``（页面取消勾选）——用户明确要全量重抽；
    * 批次划分签名对不上——切片增删、批大小改动、换抽取模型都会让「第 N 批」换一堆切片。
    """
    if mode != RC.RAG_KG_CKPT_RESUME:
        await RagTaskService.kg_ckpt_drop(doc_id)
        return {}
    cached = await RagTaskService.kg_ckpt_load(doc_id)
    if not cached:
        return {}
    if str(cached.get(RC.KG_CKPT_FIELD_META) or "") != want_sig:
        log.info(f"图谱断点的批次划分已变（切片增删/每批片数/抽取模型），整份作废: doc={doc_id}")
        await RagTaskService.kg_ckpt_drop(doc_id)
        return {}
    return cached


async def _save_ckpt(doc_id: int, no: int,
                     result: tuple[list[dict], list[dict], list[str]], *,
                     stamp: str, sig: str) -> bool:
    """把这一批的归一化结果落进断点（抽完即写；写不进去不影响本次构建，只是下次多抽一批）。

    连 ``meta`` 一起写：中途崩了也要留下一份「这批编号对得上哪个划分」的自认凭证，
    否则下次只能整份丢掉。
    """
    entities, relations, notes = result
    value = kg_ckpt.pack(entities, relations, notes, stamp=stamp)
    if not value:
        return False
    return await RagTaskService.kg_ckpt_save(doc_id, {
        RC.KG_CKPT_FIELD_META: sig,
        kg_ckpt.batch_field(no): value,
    })


async def _extract_batch(cfg: Any, prompt: str, *, doc_id: int, no: int,
                         total: int) -> Any:
    """一批抽取 + 原地重试（只重这一批），重试耗尽才抛错。

    上游 429、网络抖动、模型偶尔回一句非 JSON 是三类最常见失败，绝大多数第二次就好。
    没有这一层时，第 40 批的一次抖动会把前 39 批的模型调用一起打成整篇失败。
    """
    attempts = max(0, int(RC.KG_EXTRACT_BATCH_RETRIES)) + 1
    last: Optional[BaseException] = None
    for i in range(attempts):
        if i:
            delay = max(0.0, float(RC.KG_EXTRACT_RETRY_BACKOFF)) * (2 ** (i - 1))
            log.warning("图谱抽取第{}/{}批失败，{}s 后原地重试（第{}次）: doc={} {}",
                        no, total, round(delay, 1), i, doc_id, str(last)[:200])
            await asyncio.sleep(delay)
        try:
            return await RagModelService.chat_json(cfg, prompt)
        except Exception as e:  # noqa: BLE001  先把手上的错误存住，最后一次才换成结论
            last = e
    raise ValueError(f"第 {no}/{total} 批实体抽取失败"
                     f"（已重试 {attempts - 1} 次）：{str(last)[:200]}")


def _extract_message(no: int, total: int, reused: int) -> str:
    """抽取阶段的进度文案（复用了几批要说出来，否则用户以为模型又被重烧了一遍）。"""
    tail = f"，其中 {reused} 批复用断点" if reused else ""
    return f"抽取实体与关系（第 {no}/{total} 批已完成{tail}）"


def _fail_message(error: Any, progressed: dict) -> str:
    """失败文案必须交代「下次点构建从哪里接着抽」——这是断点续抽对用户的唯一回执。"""
    base = f"图谱构建失败：{str(error)[:200]}"
    total = int(progressed.get("total") or 0)
    done = int(progressed.get("done") or 0)
    if total <= 0 or done <= 0:
        return base[:500]
    return (f"{base}（已抽取 {done}/{total} 批，断点已存，"
            f"下次构建从第 {done + 1} 批接着抽）")[:500]


async def _load_chunks(doc_id: int) -> list[dict]:
    """按 chunk_index 顺序取该文档**全部**可用切片（不再封顶片数，所有切片都要进抽取）。

    只取 available=1 的：人工停用的切片不该再进图谱，否则页面刚把一段错别字原文
    停用掉，图上还挂着从它抽出来的实体。

    分批粒度（一次调用喂几片）在调用方，见 rag_settings.graph_extract_batch_chunks。
    """
    async with mysql_client.get_session() as session:
        rows = (await session.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.content,
                   DocumentChunk.title_path)
            .where(DocumentChunk.doc_id == int(doc_id),
                   DocumentChunk.available == 1)
            .order_by(DocumentChunk.chunk_index.asc()))).mappings().all()
    return [dict(r) for r in rows if str(r.get("content") or "").strip()]


def _merge_batches(drawn: Sequence[tuple[list[dict], list[dict], list[str]]]
                   ) -> tuple[list[dict], list[dict], list[str]]:
    """把各批抽取结果并成一次写入的实体表与关系表。

    分批只是因为单条 prompt 装不下那么多片，不该让同一个实体在不同批里变成两个节点：
    实体按名字合并（溯源切片取并集、类型以第一次出现的为准），关系按
    ``(head, tail, relation)`` 去重，端点类型一律回落到合并后的实体表——批与批给出的
    类型不一致时也必须与最终 MERGE 用的那个一致，否则图上会裂成两个同名词节点（口径 4）。
    """
    notes: list[str] = []
    by_name: dict[str, dict] = {}
    for entities, _rels, batch_notes in drawn:
        notes.extend(batch_notes)
        for item in entities:
            kept = by_name.get(item["name"])
            if kept is None:
                by_name[item["name"]] = {**item,
                                         "aliases": list(item.get("aliases") or []),
                                         "chunk_ids": list(item["chunk_ids"])}
                continue
            if kept["type"] != item["type"]:
                notes.append(f"实体「{item['name']}」在不同批次里类型不同，"
                             f"按「{kept['type']}」入库")
            if not kept.get("summary"):
                kept["summary"] = item.get("summary")
            kept["aliases"] = list(dict.fromkeys(
                kept["aliases"] + [str(a) for a in (item.get("aliases") or []) if a]))
            kept["chunk_ids"] = list(dict.fromkeys(kept["chunk_ids"] + list(item["chunk_ids"])))

    type_of = {name: e["type"] for name, e in by_name.items()}
    relations: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for _entities, rels, _batch_notes in drawn:
        for rel in rels:
            key = (rel["head"], rel["tail"], rel["relation"])
            if key in seen:
                continue
            seen.add(key)
            relations.append({**rel,
                              "head_type": type_of.get(rel["head"], rel["head_type"]),
                              "tail_type": type_of.get(rel["tail"], rel["tail_type"])})
    return list(by_name.values()), relations, notes


def _normalize(payload: Any, es_ids: dict[int, str]) -> tuple[list[dict], list[dict], list[str]]:
    """模型返回的 JSON → kg_store 的入参形态，并记下丢掉了什么（降级必须看得见）。

    只认 ``entities/relations`` 之外的同义写法也一并兼容（nodes/triples/edges），
    因为换一个大模型就要改一次代码里的字段名的话，内置的 ``graph_extract_prompt`` 模板就白给了。

    :param es_ids: ``{chunk_index: ES _id}``——本文档全部可用切片的溯源表，
        模型报回来的切片编号只有在这张表里才能换成溯源地址
    :raises ValueError: 返回结构不是 JSON 对象
    """
    notes: list[str] = []
    data: Any = payload
    if isinstance(data, list):
        # 有的模型直接回一个实体数组：按实体理解，关系为空
        data = {"entities": data}
    if not isinstance(data, dict):
        raise ValueError(f"抽取结果不是 JSON 对象（拿到 {type(data).__name__}）")

    raw_entities = _as_list(data.get("entities")) or _as_list(data.get("nodes"))
    raw_relations = (_as_list(data.get("relations")) or _as_list(data.get("triples"))
                     or _as_list(data.get("edges")))
    if not raw_entities:
        # 空结果原样返回：这段内容没有可成图的事实不等于抽取失败，整篇为空也由调用方按成功收口
        return [], [], ["某一批没抽出实体（这段切片可能是目录/页眉，已跳过）"]

    entities: list[dict] = []
    type_of: dict[str, str] = {}
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("entity") or "").strip()
        if not name:
            continue
        etype = kg_store.entity_type_of(item.get("type") or item.get("entity_type"))
        # 同一名字被写成两种类型：先到先得，后一个不再建节点（否则图上裂成两个同名实体）
        if name in type_of:
            if type_of[name] != etype:
                notes.append(f"实体「{name}」出现多种类型，按「{type_of[name]}」入库")
            continue
        type_of[name] = etype
        entities.append({"name": name, "type": etype,
                         "summary": item.get("summary") or item.get("description"),
                         "aliases": item.get("aliases") or [],
                         "chunk_ids": _mention_chunks(item, es_ids)})

    relations: list[dict] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        head = str(item.get("head") or item.get("source") or "").strip()
        tail = str(item.get("tail") or item.get("target") or "").strip()
        rel = str(item.get("relation") or item.get("name") or "").strip()
        if not head or not tail or head == tail:
            continue
        if not rel:
            notes.append(f"关系「{head}→{tail}」没有描述，已丢弃")
            continue
        if head not in type_of or tail not in type_of:
            # 端点不在实体表里就不能猜它的类型：猜错就会 MERGE 出另一个同名节点
            notes.append(f"关系「{head}-{rel}->{tail}」的端点不在实体列表里，已丢弃")
            continue
        relations.append({"head": head, "tail": tail, "relation": rel,
                          "head_type": type_of[head], "tail_type": type_of[tail]})

    lost = sum(1 for e in entities if not e["chunk_ids"])
    if lost:
        notes.append(f"{lost} 个实体没给出有效切片编号（只能入库、无法溯源）")
    return entities, relations, notes


def _as_list(value: Any) -> list:
    """字段容错：只认数组，给了字典/字符串当没这个键（不让一个脏字段毁掉整次抽取）。"""
    return list(value) if isinstance(value, list) else []


def _mention_chunks(item: dict, es_ids: dict[int, str]) -> list[str]:
    """模型给的切片编号 → ES 的 ``_id``（kg_store 靠它建 MENTIONED_IN 溯源边）。

    只认本次发出去的那批编号：模型偶尔会编一个不存在的 [切片99]，留着它就会写出
    一个指向空切片的桩节点，页面点过去什么都没有。
    """
    raw: Any = item.get("chunk_indexes")
    if raw is None:
        raw = item.get("chunks") if item.get("chunks") is not None else item.get("chunk_ids")
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out: list[str] = []
    for value in raw:
        try:
            es_id = es_ids.get(int(value))
        except (TypeError, ValueError):
            continue
        if es_id:
            out.append(es_id)
    return list(dict.fromkeys(out))


async def _save_state(doc_id: int, state: int, *, session=None) -> None:
    """只写 graph_state（图谱构建态与解析状态机互不干涉，见 kb_entity 列注释）。"""
    async with maybe_session(session) as s:
        await s.execute(update(Document).where(
            Document.doc_id == int(doc_id)).values(graph_state=int(state)))
        await s.commit()


# =====================================================================================
# 内部：契约转换
# =====================================================================================

def _node_dto(row: dict) -> dict:
    """kg_store 的实体字典（snake）→ 前端契约（camel）。"""
    return GraphNodeResp(
        id=str(row.get("id") or ""), name=str(row.get("name") or ""),
        label=RC.KG_LABEL_ENTITY, entity_type=str(row.get("type") or ""),
        kb_id=int(row.get("kb_id") or 0), summary=str(row.get("summary") or ""),
        aliases=[str(a) for a in (row.get("aliases") or []) if a],
        weight=int(row.get("degree") or row.get("score") or 0)).dump()


def _edge_dto(row: dict) -> dict:
    """kg_store 的边字典 → 前端契约（RELATED_TO 的边类型固定，具体关系描述放 name）。"""
    return GraphEdgeResp(
        source=str(row.get("source") or ""), target=str(row.get("target") or ""),
        relation=RC.KG_REL_RELATION, name=str(row.get("relation") or "") or None,
        kb_id=int(row.get("kb_id") or 0),
        doc_ids=[int(d) for d in (row.get("doc_ids") or []) if d],
        weight=int(row.get("weight") or 0)).dump()


__all__ = ["RagGraphService"]
