# -*- coding: utf-8 -*-
"""图谱那一路的检索（需求 9b）：问句抽词 → 实体/关系两路 kNN → 邻域展开 → 回捞原文。

为什么向量匹配不查 Neo4j
------------------------
Neo4j 侧只有实体全文索引、没有向量（见 kg_store 的建索引段）；而用户问句里的说法与
实体名常常不完全相等（「东华理工」↔「东华理工大学」），按分词命中会大量漏召。所以
**匹配只查 ES 的 rag_kg_vector 投影**，拿到实体名再回 Neo4j 展开关系与溯源原文——
投影是 Neo4j 的可重建副本（见 kg_vector），这里只读不写。

与 LightRAG 的对应关系
----------------------
- low-level search：拿实体词查实体向量库（本页问题的「谁」）；
- high-level search：拿关系词查关系向量库（「之间发生了什么」）；
- 两路命中后按 depth 沿 RELATED_TO 扩邻域、按 scale 控制规模，再顺 MENTIONED_IN
  回捞原文切片当引用依据。深度与规模都是页面可传参数，越界夹到上限，不做隐式放大。

三段各自能失败
--------------
抽词（对话模型）、匹配（ES）、展开与原文（Neo4j）三段独立降级：任何一段挂了都只是
「图谱这一路少一块料」，切片那一路照常返回。图谱是**叠加项**，不是主链路的前置条件。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_es import es_client, kg_source
from common.common_log.log_init import log
from common.common_neo4j import kg_store
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.rag_model import RagModelService


def clamp_depth(value: Any) -> int:
    """图谱扩散深度：页面可传，越界夹到 [1, KG_RETRIEVE_MAX_DEPTH]。"""
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = RC.KG_RETRIEVE_DEFAULT_DEPTH
    return max(1, min(depth, RC.KG_RETRIEVE_MAX_DEPTH))


def clamp_scale(value: Any) -> int:
    """每一路向量召回的条数（也是进上下文的实体数上限）。"""
    try:
        scale = int(value)
    except (TypeError, ValueError):
        scale = RC.KG_RETRIEVE_DEFAULT_SCALE
    return max(1, min(scale, RC.KG_RETRIEVE_MAX_SCALE))


def _clean_words(items: Any, *, limit: int) -> list[str]:
    """模型给的词表 → 去空去重（保序）。抽取型模型偶尔回对象数组，两种形态都认。"""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(items, (list, tuple, set)):
        return out
    for one in items:
        if isinstance(one, dict):
            word = str(one.get("name") or one.get("relation") or "").strip()
        else:
            word = str(one or "").strip()
        if not word or len(out) >= limit:
            continue
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


async def extract_query_terms(chat_cfg: Any, question: str,
                              entity_types: Optional[Sequence[str]] = None) -> dict[str, Any]:
    """问句 → 查询词（entities / relations）。

    entity_types：这个库的实体范围（自定义 prompt_types，与建图时同一口径）；传入则
    只按这些类型从问句抽词，与图里存过的实体名对齐；留空按系统默认。

    整句兜底必须有：图谱里存的是抽取时见过的名词，用户问「这两家是什么关系」里
    一个实体名都没有，不兜底这一路就直接空转；兜底之后最差也退化成「按整句做语义匹配」，
    与 LightRAG 的 low-level 查询同一个形态。
    """
    entities: list[str] = []
    relations: list[str] = []
    try:
        data = await RagModelService.chat_json(
            chat_cfg, settings.build_kg_query_extract_prompt(question, entity_types=entity_types))
        if isinstance(data, dict):
            entities = _clean_words(data.get("entities"), limit=RC.KG_RETRIEVE_MAX_SCALE)
            relations = _clean_words(data.get("relations"), limit=RC.KG_RETRIEVE_MAX_SCALE)
    except Exception as e:  # noqa: BLE001  问句解析失败只是少一层结构化，整句照样能查
        log.warning(f"图谱检索的问句抽词失败，改为整句匹配: {e}")
    fallback = not entities
    if fallback:
        entities = [str(question or "").strip()[:200]]
    return {"entities": entities, "relations": relations, "fallback": fallback}


async def _match(kb_ids: Sequence[int], words: Sequence[str], *, kind: str,
                 embed_cfg: Any, scale: int, notes: list[str]) -> list[dict]:
    """一批查询词在一组库里做同 kind 的 kNN，跨词合并后按余弦分取前 scale 条。

    同一个实体可能被两个近义词各命中一次（投影 _id 相同），按 _id 保最高分而不是追加两条，
    否则上下文里会出现同一个实体的两份摘要，页面也显示成两条重复结果。
    """
    ids = [int(i) for i in (kb_ids or []) if i]
    terms = [str(w or "").strip() for w in (words or []) if str(w or "").strip()]
    if not ids or not terms:
        return []
    vectors = await RagModelService.embed_texts(embed_cfg, terms)
    best: dict[str, dict] = {}
    for term, vec in zip(terms, vectors):
        if not vec or all(abs(float(x)) < 1e-12 for x in vec):
            continue
        try:
            hits = await es_client.kg_search_vector(
                ids, vec, kind=kind, size=scale, min_score=RC.KG_VECTOR_MATCH_MIN_SCORE)
        except Exception as e:  # noqa: BLE001  投影索引不在/查询失败只降图谱这一路
            notes.append(f"图谱{RC.KG_VECTOR_KIND_LABELS.get(kind, kind)}匹配失败：{str(e)[:120]}")
            return []
        for hit in hits:
            kg_id = str(hit.get("es_id") or "")
            if not kg_id:
                continue
            cur = best.get(kg_id)
            if cur is None or float(hit.get("score") or 0.0) > float(cur.get("score") or 0.0):
                best[kg_id] = hit
    return sorted(best.values(), key=lambda h: -float(h.get("score") or 0.0))[:scale]


def _entity_dto(hit: dict) -> dict:
    """投影命中 → 页面/上下文用的实体信息（refs 里带着 Neo4j 那份摘要）。"""
    src = kg_source(hit)
    refs = src.get("refs") if isinstance(src.get("refs"), dict) else {}
    return {"name": str(src.get(RC.KG_FIELD_NAME) or refs.get("name") or ""),
            "type": str(src.get(RC.KG_FIELD_ETYPE) or ""),
            "kb_id": int(src.get(RC.KG_FIELD_KB) or 0),
            "summary": str(refs.get("summary") or ""),
            "aliases": [str(a) for a in (refs.get("aliases") or []) if a],
            "score": round(float(src.get("score") or 0.0), 6)}


def _relation_dto(hit: dict) -> dict:
    src = kg_source(hit)
    refs = src.get("refs") if isinstance(src.get("refs"), dict) else {}
    head = str(refs.get("head") or "")
    tail = str(refs.get("tail") or "")
    rel = str(src.get(RC.KG_FIELD_ETYPE) or refs.get("relation") or "")
    return {"head": head, "relation": rel, "tail": tail,
            "text": str(src.get(RC.KG_FIELD_TEXT) or ""),
            "kb_id": int(src.get(RC.KG_FIELD_KB) or 0),
            "score": round(float(src.get("score") or 0.0), 6)}


def triple_of(relation: dict) -> str:
    """一条关系 → 「头 -[关系]-> 尾」文本（上下文与页面展示同一写法）。"""
    head = str(relation.get("head") or "").strip()
    tail = str(relation.get("tail") or "").strip()
    rel = str(relation.get("relation") or "").strip()
    if not head or not tail:
        return str(relation.get("text") or "").strip()
    return f"{head} -[{rel}]-> {tail}"


async def recall(groups: Sequence[dict], *, question: str, chat_model_id: int = 0,
                 depth: Any = None, scale: Any = None,
                 with_context: bool = True) -> dict:
    """图谱那一路的完整召回。

    :param groups: ``[{"kb_ids":[...], "embed_model_id":int,
                        "extract_model_id":int(选填), "entity_types":[...](选填)}]``。
        匹配仍**按向量模型分组**（实体/关系投影与切片共用知识库自己的向量空间，两个不同
        模型编出的向量不能交叉比距离）；抽词模型与实体范围**按组各取**：组里带了
        extract_model_id 就用它（每个知识库用自己建图时那套图谱模型+实体范围抽词，
        这是深度探索 kb_search 的口径），没带就回退 chat_model_id（检索页整批共用一个
        对话模型的老口径）；两者都没有的组直接跳过它的图谱那一路。
    :param chat_model_id: 缺省抽词模型（组未带 extract_model_id 时才用它）；可为 0。
    :param with_context: 是否回捞原文切片（关掉只出实体与三元组，给「只想看有哪些实体」的场景用）。
    :return: {"entities","relations","edges","triples","sources","es_ids","terms","truncated","warnings"}
        ``sources`` 是切片那一路拿不到的东西——**图谱叠加到检索结果上的行**，
        形态 ``[{"es_id","kb_id","entity","score"}]``，score 取命中实体的向量分，
        调用方据此把它和 BM25+kNN 的行一起排序（见 retrieval_service 的 _merge_graph）。
        ``edges`` 是 ``triples`` 的结构化那份；``terms`` 汇总各组抽出的实体词/关系词并集。
    """
    depth = clamp_depth(depth)
    scale = clamp_scale(scale)
    notes: list[str] = []
    kb_ids = sorted({int(i) for g in groups or [] for i in (g.get("kb_ids") or []) if int(i or 0)})
    empty = {"entities": [], "relations": [], "edges": [], "triples": [], "sources": [],
             "es_ids": [],
             "terms": {"entities": [], "relations": []}, "truncated": False,
             "warnings": notes}
    if not kb_ids or not str(question or "").strip():
        return empty

    # 抽词按 (抽取模型, 实体范围) 去重：同配置的多个库共用一次大模型调用；
    # 组与组之间互不依赖，且都是网络 I/O（抽取/向量化/ES kNN），所以分两阶段并发跑，
    # 而不是把延迟乘以组数：先把去重后的抽词并发完，再把每组的向量匹配并发完。
    unique_tkeys: list[tuple] = []
    seen_tkey: set[tuple] = set()
    # valid_groups 每项：(g_ids, tkey, embed_model_id)（每组都要单独匹配，只有抽词去重）
    valid_groups: list[tuple] = []
    for group in groups or []:
        g_ids = [int(i) for i in (group.get("kb_ids") or []) if int(i or 0)]
        if not g_ids:
            continue
        ex_model = int(group.get("extract_model_id") or 0) or int(chat_model_id or 0)
        if not ex_model:
            notes.append(f"知识库 {g_ids} 未配置图谱抽取模型，跳过其图谱那一路")
            continue
        etypes = group.get("entity_types") or None
        tkey = (ex_model, tuple(etypes) if etypes else None)
        if tkey not in seen_tkey:
            seen_tkey.add(tkey)
            unique_tkeys.append(tkey)
        valid_groups.append((g_ids, tkey, int(group.get("embed_model_id") or 0)))

    async def _extract_one(tkey: tuple) -> Optional[dict]:
        """一个 (抽取模型, 实体范围) 的抽词；失败由 gather 接成异常，只影响这一 key。"""
        ex_model, etypes = tkey
        chat_cfg = await RagModelService.require_config(ex_model, "图谱抽取模型")
        RagModelService._ensure_extract_capable(chat_cfg)
        return await extract_query_terms(
            chat_cfg, question, entity_types=list(etypes) if etypes else None)

    # 阶段 A：去重后的抽词并发（return_exceptions=True 让单 key 失败不连累其他 key）
    extracted = await asyncio.gather(
        *[_extract_one(tk) for tk in unique_tkeys], return_exceptions=True)
    term_cache: dict[tuple, Optional[dict]] = {}
    extract_err: dict[tuple, str] = {}
    for tk, res in zip(unique_tkeys, extracted):
        if isinstance(res, BaseException):
            term_cache[tk] = None
            extract_err[tk] = str(res)[:120]
        else:
            term_cache[tk] = res

    async def _match_one(g_ids: list[int], tkey: tuple,
                         embed_model_id: int) -> tuple:
        """一组的向量匹配（实体+关系两路）；局部 notes 回主线程按组顺序合并。"""
        local: list[str] = []
        terms = term_cache.get(tkey)
        if terms is None:
            local.append(f"知识库 {g_ids} 问句抽词失败：{extract_err.get(tkey, '')}")
            return local, terms, [], []
        try:
            embed_cfg = await RagModelService.require_config(embed_model_id, "向量模型")
        except ValueError as e:  # 这一组没有可用的向量模型，只跳过它的图谱匹配
            local.append(str(e)[:120])
            return local, terms, [], []
        e_hits = await _match(g_ids, terms["entities"],
                              kind=RC.KG_VECTOR_KIND_ENTITY,
                              embed_cfg=embed_cfg, scale=scale, notes=local)
        r_hits = []
        if terms["relations"]:
            r_hits = await _match(g_ids, terms["relations"],
                                  kind=RC.KG_VECTOR_KIND_RELATION,
                                  embed_cfg=embed_cfg, scale=scale, notes=local)
        return local, terms, e_hits, r_hits

    # 阶段 B：每组的向量匹配并发；匹配本身已按组隔离，并发只改变完成顺序不改变结果
    matched = await asyncio.gather(*[_match_one(g, tk, em) for (g, tk, em) in valid_groups])

    union_ent: list[str] = []
    union_rel: list[str] = []
    any_fallback = False
    ent_hits: list[dict] = []
    rel_hits: list[dict] = []
    for local, terms, e_hits, r_hits in matched:
        notes.extend(local)
        if terms is None:
            continue
        any_fallback = any_fallback or bool(terms.get("fallback"))
        for w in terms["entities"]:
            if w not in union_ent:
                union_ent.append(w)
        for w in terms["relations"]:
            if w not in union_rel:
                union_rel.append(w)
        ent_hits.extend(e_hits)
        rel_hits.extend(r_hits)

    # 汇总各组抽出的词（返回体与页面展示用；匹配已在上面按组做完）
    terms = {"entities": union_ent[:RC.KG_RETRIEVE_MAX_SCALE],
             "relations": union_rel[:RC.KG_RETRIEVE_MAX_SCALE],
             "fallback": any_fallback}

    # 同名同类可能来自两个库/两次命中：按 (kb_id, type, name) 取最高分，顺序即相关度
    entities: dict[tuple, dict] = {}
    for hit in ent_hits:
        dto = _entity_dto(hit)
        if not dto["name"]:
            continue
        key = (dto["kb_id"], dto["type"], dto["name"])
        cur = entities.get(key)
        if cur is None or dto["score"] > cur["score"]:
            entities[key] = dto
    ent_list = sorted(entities.values(), key=lambda e: -e["score"])[:scale]

    relations: dict[tuple, dict] = {}
    for hit in rel_hits:
        dto = _relation_dto(hit)
        if not dto["text"]:
            continue
        key = (dto["kb_id"], dto["head"], dto["relation"], dto["tail"])
        cur = relations.get(key)
        if cur is None or dto["score"] > cur["score"]:
            relations[key] = dto
    rel_list = sorted(relations.values(), key=lambda r: -r["score"])

    triples = [triple_of(r) for r in rel_list if triple_of(r)][:RC.KG_CONTEXT_MAX_TRIPLES]
    # 关系同时给一份结构化的（只留有头有尾的，纯描述型关系在图上没有落点）
    edges: list[dict] = [
        {"head": r["head"], "relation": r["relation"], "tail": r["tail"],
         "kb_id": r["kb_id"], "score": r["score"]}
        for r in rel_list[:RC.KG_CONTEXT_MAX_TRIPLES] if r["head"] and r["tail"]
    ]
    truncated = len(rel_list) > RC.KG_CONTEXT_MAX_TRIPLES

    # 邻域展开：以命中实体为中心按 depth 跳，把「问句里没提但相关」的关系也带进上下文
    centers = [e["name"] for e in ent_list]
    if centers:
        try:
            sub = await kg_store.subgraph(kb_ids, centers=centers, depth=depth,
                                          limit=max(scale * 4, RC.KG_VIZ_DEFAULT_LIMIT))
            seen_triples = set(triples)
            for edge in sub.get("edges") or []:
                text = f"{_node_name(edge.get('source'))} -[{edge.get('relation')}]-> "\
                       f"{_node_name(edge.get('target'))}"
                if text in seen_triples:
                    continue
                seen_triples.add(text)
                if len(triples) >= RC.KG_CONTEXT_MAX_TRIPLES:
                    truncated = True
                    break
                triples.append(text)
                # 邻域扩出来的边也要进图：问句里没提关系词时，图上唯一的关系来源就是这批
                e_head, e_tail = _node_name(edge.get("source")), _node_name(edge.get("target"))
                if e_head and e_tail:
                    edges.append({"head": e_head,
                                  "relation": str(edge.get("relation") or ""),
                                  "tail": e_tail,
                                  "kb_id": _node_kb(edge.get("source")), "score": 0.0})
            truncated = truncated or bool(sub.get("truncated"))
        except Exception as e:  # noqa: BLE001  展开失败只用命中的那批关系，不必整路作废
            notes.append(f"图谱邻域展开失败（只用直接命中的关系）：{str(e)[:120]}")

    sources: list[dict] = []
    if with_context and centers:
        sources = await _source_chunks(kb_ids, ent_list, notes=notes)

    log.info("图谱检索完成: kb={} 实体词={} 命中实体={} 关系={} 原文={} 深度={} 规模={}",
             kb_ids, len(terms["entities"]), len(ent_list), len(triples), len(sources),
             depth, scale)
    return {"entities": ent_list, "relations": rel_list[:scale], "edges": edges,
            "triples": triples,
            "sources": sources, "es_ids": [s["es_id"] for s in sources],
            "terms": terms, "truncated": truncated, "warnings": notes}


def _node_kb(node_id: Any) -> int:
    """子图节点 id（``kb_id:类型:实体名``）→ 库 id（图上要能说清这个实体出自哪个库）。"""
    parts = str(node_id or "").split(":")
    try:
        return int(parts[0]) if parts else 0
    except ValueError:
        return 0


def _node_name(node_id: Any) -> str:
    """子图节点 id（``kb_id:类型:实体名``）→ 实体名（三元组给人看，不带内部主键）。"""
    parts = str(node_id or "").split(":")
    return parts[-1] if parts else ""


async def _source_chunks(kb_ids: Sequence[int], entities: Sequence[dict], *,
                         notes: list[str]) -> list[dict]:
    """按命中实体回捞它出现过的切片（MENTIONED_IN → Chunk.es_id），带规模上限。

    每个实体只取前几片、总量再卡一道：图谱一路的价值是把「谁和谁有什么关系、
    关系出自哪一段」说清楚，不是把整库正文塞进问答 prompt。

    分数拿实体自己的向量分（切片在图谱一路里没有独立的相似度——它是被实体“拽”出来的），
    调用方才能把图谱行与 BM25+kNN 行放到同一个尺子下排序。
    """
    found: dict[str, dict] = {}
    picked = 0
    for one in entities:
        if picked >= RC.KG_CONTEXT_MAX_CHUNKS:
            notes.append(f"图谱原文按前 {RC.KG_CONTEXT_MAX_CHUNKS} 条切片取用，"
                         f"其余实体的原文未进上下文")
            break
        name = str(one.get("name") or "")
        if not name:
            continue
        # 实体节点本身带 kb_id（同名同类只在单库内合并），按它查比拿整批白名单去查便宜
        one_kb = int(one.get("kb_id") or 0)
        try:
            rows = await kg_store.entity_sources(
                [one_kb] if one_kb else list(kb_ids), name,
                entity_type=str(one.get("type") or "") or None,
                limit=RC.KG_CONTEXT_CHUNKS_PER_ENTITY)
        except Exception as e:  # noqa: BLE001  单个实体溯源失败不影响别的实体
            notes.append(f"实体「{name}」的原文回捞失败：{str(e)[:100]}")
            continue
        score = float(one.get("score") or 0.0)
        room = RC.KG_CONTEXT_MAX_CHUNKS - picked
        for row in rows[:max(0, room)]:
            es_id = str(row.get("es_id") or "")
            if not es_id:
                continue
            cur = found.get(es_id)
            if cur is None:
                found[es_id] = {"es_id": es_id,
                                "kb_id": int(row.get("kb_id") or one_kb or 0),
                                "entity": name, "score": score}
            elif score > float(cur.get("score") or 0.0):
                # 同一片被两个实体提及：归到分更高的那个实体名下（解释结果时用它）
                cur["score"] = score
                cur["entity"] = name
        picked += min(len(rows), max(0, room))
    return sorted(found.values(), key=lambda s: -float(s.get("score") or 0.0))


def format_entities(entities: Sequence[dict]) -> str:
    """实体清单 → 一段可进 prompt 的文字（含摘要，页面「命中实体」也用这个形态）。"""
    parts: list[str] = []
    for one in entities:
        name = str(one.get("name") or "").strip()
        if not name:
            continue
        etype = str(one.get("type") or "").strip()
        summary = str(one.get("summary") or "").strip()
        line = f"{name}（{etype}）" if etype else name
        if summary:
            line = f"{line}：{summary}"
        parts.append(line)
    return "、".join(parts)


__all__ = ["recall", "extract_query_terms", "clamp_depth", "clamp_scale",
           "triple_of", "format_entities"]
