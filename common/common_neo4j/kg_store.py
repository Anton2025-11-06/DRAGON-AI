# -*- coding: utf-8 -*-
"""知识图谱读写门面（SPEC §10.3 / §7.6）：把 Cypher 收在这一个文件里，业务层只见数据结构。

节点与关系模型
--------------
(:RagDocument {kb_id, doc_id, org_id, title, update_time})
(:RagChunk    {es_id, kb_id, doc_id, org_id, chunk_index, content, title_path})   ← 溯源锚点
(:Entity      {kb_id, org_id, name, type, summary, aliases})                      ← 同库同名同类合并

(Entity)-[:MENTIONED_IN {kb_id, doc_id}]->(Chunk)      实体出自哪一片原文
(Chunk)  -[:EXTRACTED_FROM]->(Document)                切片属于哪篇文档
(Entity)-[:RELATED_TO {relation, kb_id, doc_ids}]->(Entity)   实间关系（doc_ids 记支撑该边的文档）

三条硬口径
----------
1. **一律带 kb_id 白名单**：Neo4j 里没有任何权限概念（SPEC §13.4 底层裸查），
   kb_ids 由上层 ACL 算好后传下来；白名单为空即返回空结果，绝不退化成全图扫描。
2. **同库内实体合并、跨库不合并**：`MERGE (kb_id, name, type)`。不同知识库里的
   「华为」可能一个是产品一个是公司，合库只会让图谱变成一团糊。
3. **删文档要能回收**：关系上记 `doc_ids`，删某篇文档时把它从支撑列表里摘掉，
   列表空了才删边；实体按「还有没有 MENTIONED_IN 溯源」判孤儿。否则重解析一次文档，
   图谱里就多一份永远删不掉的幽灵关系。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_es.index_mapping import parse_es_doc_id
from common.common_log.log_init import log
from common.common_neo4j.client import FULLTEXT_INDEX, Neo4jError, neo4j_client

# 抽取出的关系若没给实体类型，落到这个类型上（与 KG_ENTITY_TYPES 的最后一项同值）
DEFAULT_ENTITY_TYPE = RC.KG_ENTITY_TYPES[-1]
# 可视化深度上限：再多一跳节点数指数级膨胀，页面也画不下
MAX_DEPTH = 3
# 图谱不复制正文：切片正文与实体摘要同尺度截断，全文一律回 ES 取
PREVIEW_CHARS = 500
# 单实体别名上限：模型一发一大串，展示与检索都用不上这么多
ALIAS_LIMIT = 10
# 关系描述长度上限：图上边的标签就那么大，长了也放不下
RELATION_CHARS = 100


def _now() -> str:
    """时间戳统一落成可读字符串：驱动返回的 DateTime 过一遍 FastAPI 序列化会报错。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_kb_ids(kb_ids: Sequence[int]) -> list[int]:
    """白名单规范化：去重去非法（脏 id 丢掉比整条查询报错好），返回空就是「一个库都不给查」。"""
    out: list[int] = []
    for one in kb_ids or []:
        try:
            kb = int(one)
        except (TypeError, ValueError):
            continue
        if kb not in out:
            out.append(kb)
    return out


def _entity_key(name: str, etype: str, kb_id: int) -> str:
    """节点 id：与实体约束的 MERGE 键同三元组，前端拿它当唯一 key。"""
    return f"{kb_id}:{etype or DEFAULT_ENTITY_TYPE}:{name}"


# =====================================================================================
# 一、骨架节点（文档 / 切片）
# =====================================================================================
async def ensure_document(kb_id: int, doc_id: int, *, org_id: int = 0,
                          title: str = "") -> None:
    """建/更新文档节点（图谱构建的第一步，切片与实体都挂在它下面）。"""
    await neo4j_client.write(
        f"MERGE (d:{RC.KG_LABEL_DOC} {{kb_id: $kb_id, doc_id: $doc_id}}) "
        f"SET d.org_id = $org_id, d.title = $title, d.update_time = $now",
        {"kb_id": int(kb_id), "doc_id": int(doc_id), "org_id": int(org_id),
         "title": title or "", "now": _now()})


async def _ensure_chunks(kb_id: int, doc_id: int, chunks: Sequence[dict], *,
                         org_id: int = 0) -> int:
    """建/更新切片节点并挂到文档上：`[{es_id, chunk_index, content, title_path}]`。

    切片节点是「实体出自哪段原文」的锚点，只存溯源所需的少量信息，
    正文截断到 PREVIEW_CHARS——完整内容永远回 ES 取，两处不重复存长文本。
    """
    rows = [{"es_id": str(c.get("es_id") or ""),
             "chunk_index": int(c.get("chunk_index") or 0),
             "content": str(c.get("content") or "")[:PREVIEW_CHARS],
             "title_path": str(c.get("title_path") or "")}
            for c in chunks or [] if c.get("es_id")]
    if not rows:
        return 0
    await neo4j_client.write(
        f"UNWIND $rows AS row "
        f"MERGE (c:{RC.KG_LABEL_CHUNK} {{es_id: row.es_id}}) "
        f"SET c.org_id = $org_id, c.kb_id = $kb_id, c.doc_id = $doc_id, "
        f"    c.chunk_index = row.chunk_index, c.content = row.content, "
        f"    c.title_path = row.title_path, c.update_time = $now "
        f"WITH c MATCH (d:{RC.KG_LABEL_DOC} {{kb_id: $kb_id, doc_id: $doc_id}}) "
        f"MERGE (c)-[:{RC.KG_REL_EXTRACT}]->(d)",
        {"rows": rows, "kb_id": int(kb_id), "doc_id": int(doc_id), "org_id": int(org_id),
         "now": _now()})
    return len(rows)


# =====================================================================================
# 二、图谱写入（LLM 抽取结果落库）
# =====================================================================================
async def upsert_graph(kb_id: int, doc_id: int, entities: Sequence[dict],
                       relations: Sequence[dict], *, chunks: Optional[Sequence[dict]] = None,
                       org_id: int = 0, replace: bool = True) -> dict:
    """一篇文档的抽取结果落库：实体 → MENTIONED_IN 切片 → RELATED_TO 关系。

    :param entities: `[{"name","type","summary","aliases":[..],"chunk_ids":[es_id,..]}]`
    :param relations: `[{"head","tail","relation","head_type","tail_type"}]`
        头尾实体必须出现在 entities 里（图谱抽取的提示词已经要求先列实体再列三元组）；
        没给 type 的按 DEFAULT_ENTITY_TYPE 落，与实体侧的缺省值一致才不会裂成两个节点。
    :param chunks: 该批实体引用到的切片（缺省时切片节点靠 chunk_ids 现场 MERGE 出桩节点）
    :param replace: True 时先按文档清理上一轮的图（重跑同一文档不叠加历史抽取结果）
    :return: {"entities","relations","mentions","chunks"} 四个计数（写进度与页面回显）
    """
    kb_id, doc_id = int(kb_id), int(doc_id)
    if replace:
        await delete_by_doc(kb_id, doc_id, keep_document=True)
    if chunks:
        await _ensure_chunks(kb_id, doc_id, chunks, org_id=org_id)

    ents = _norm_entities(entities)
    rels = _norm_relations(relations)
    if not ents and not rels:
        return {"entities": 0, "relations": 0, "mentions": 0, "chunks": 0}
    now = _now()

    if ents:
        await neo4j_client.write(
            f"UNWIND $rows AS row "
            f"MERGE (e:{RC.KG_LABEL_ENTITY} {{kb_id: $kb_id, name: row.name, type: row.type}}) "
            f"ON CREATE SET e.org_id = $org_id, e.summary = row.summary, "
            f"              e.aliases = row.aliases, e.create_time = $now "
            f"ON MATCH SET e.summary = coalesce(row.summary, e.summary), "
            f"             e.org_id = $org_id, e.update_time = $now",
            {"rows": ents, "kb_id": kb_id, "org_id": int(org_id), "now": now})
        mentions = await _upsert_mentions(kb_id, doc_id, ents, org_id=org_id)
    else:
        mentions = 0

    if rels:
        await neo4j_client.write(
            f"UNWIND $rows AS row "
            f"MERGE (h:{RC.KG_LABEL_ENTITY} {{kb_id: $kb_id, name: row.head, type: row.head_type}}) "
            f"MERGE (t:{RC.KG_LABEL_ENTITY} {{kb_id: $kb_id, name: row.tail, type: row.tail_type}}) "
            f"MERGE (h)-[r:{RC.KG_REL_RELATION} {{relation: row.relation}}]->(t) "
            f"SET r.kb_id = $kb_id, r.update_time = $now, "
            f"    r.doc_ids = CASE WHEN r.doc_ids IS NULL THEN [$doc_id] "
            f"                     WHEN NOT $doc_id IN r.doc_ids THEN r.doc_ids + [$doc_id] "
            f"                     ELSE r.doc_ids END",
            {"rows": rels, "kb_id": kb_id, "doc_id": doc_id, "now": now})
    return {"entities": len(ents), "relations": len(rels), "mentions": mentions,
            "chunks": len(chunks or [])}


def _norm_entities(entities: Sequence[dict]) -> list[dict]:
    """实体规范化：名字去空去重，类型落在受控集合内（模型自由发挥出十种类型会让图例失效）。"""
    out: dict[tuple[str, str], dict] = {}
    for item in entities or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        etype = entity_type_of(item.get("type"))
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        chunk_ids = [str(c).strip() for c in (item.get("chunk_ids") or []) if c]
        key = (name, etype)
        exist = out.get(key)
        if exist:
            # 同一批里重复实体（模型常犯）合并而不是报错：溯源切片并集，摘要取长的
            if len(str(item.get("summary") or "")) > len(str(exist.get("summary") or "")):
                exist["summary"] = str(item.get("summary") or "")
            for cid in chunk_ids:
                if cid not in exist["chunk_ids"]:
                    exist["chunk_ids"].append(cid)
            for a in aliases:
                if a not in exist["aliases"]:
                    exist["aliases"].append(a)
            continue
        out[key] = {"name": name, "type": etype,
                    "summary": str(item.get("summary") or "")[:PREVIEW_CHARS],
                    "aliases": aliases[:ALIAS_LIMIT], "chunk_ids": chunk_ids}
    return list(out.values())


def _norm_relations(relations: Sequence[dict]) -> list[dict]:
    """关系规范化：头尾齐全、不自环、类型缺省与实体侧一致。"""
    out: dict[tuple[str, str, str], dict] = {}
    for item in relations or []:
        head = str(item.get("head") or "").strip()
        tail = str(item.get("tail") or "").strip()
        rel = str(item.get("relation") or "").strip()
        if not head or not tail or head == tail or not rel:
            continue
        key = (head, tail, rel)
        if key in out:
            continue
        out[key] = {"head": head, "tail": tail, "relation": rel[:RELATION_CHARS],
                    "head_type": entity_type_of(item.get("head_type")),
                    "tail_type": entity_type_of(item.get("tail_type"))}
    return list(out.values())


def entity_type_of(value: Any) -> str:
    """实体类型落受控集合：不在表内的一律归“其他”。

    MERGE 键里带了 type，模型今天发挥出一个「公司名称」明天写出「组织」，
    同一个实体就会在图上裂成两个节点（而且再也合不回来）。上层组装抽取结果时
    必须用同一个函数，不然实体侧与关系侧的两个缺省值会不一致。
    """
    etype = str(value or "").strip() or DEFAULT_ENTITY_TYPE
    return etype if etype in RC.KG_ENTITY_TYPES else DEFAULT_ENTITY_TYPE


async def _upsert_mentions(kb_id: int, doc_id: int, entities: Sequence[dict], *,
                           org_id: int = 0) -> int:
    """实体 → 切片 的溯源边；缺切片的先补桩节点（保证图上每个实体都能点回原文）。"""
    rows = [{"name": e["name"], "type": e["type"], "es_id": cid,
             "chunk_index": _chunk_index_of(cid)}
            for e in entities for cid in e["chunk_ids"]]
    if not rows:
        return 0
    await neo4j_client.write(
        f"UNWIND $rows AS row "
        f"MERGE (c:{RC.KG_LABEL_CHUNK} {{es_id: row.es_id}}) "
        f"ON CREATE SET c.org_id = $org_id, c.kb_id = $kb_id, c.doc_id = $doc_id, "
        f"              c.chunk_index = row.chunk_index, c.content = '' "
        f"WITH row, c MATCH (e:{RC.KG_LABEL_ENTITY} {{kb_id: $kb_id, name: row.name, "
        f"                                          type: row.type}}) "
        f"MERGE (e)-[r:{RC.KG_REL_MENTIONED}]->(c) "
        f"SET r.kb_id = $kb_id, r.doc_id = $doc_id, r.update_time = $now",
        {"rows": rows, "kb_id": int(kb_id), "doc_id": int(doc_id), "org_id": int(org_id),
         "now": _now()})
    return len(rows)


def _chunk_index_of(es_id: str) -> int:
    """从 `{kb}_{doc}_{index}` 反解 chunk_index；不是这个格式就 0（桩节点不参与排序）。"""
    try:
        return parse_es_doc_id(es_id)[2]
    except ValueError:
        return 0


# =====================================================================================
# 三、图谱读取
# =====================================================================================
async def search_entities(kb_ids: Sequence[int], keyword: str, *, limit: int = 20,
                          entity_type: Optional[str] = None) -> list[dict]:
    """实体检索（图谱页搜索框、检索页的图谱增强入口都走这里）。

    先走全文索引，命中为空或索引没建好（老库）时退化成 CONTAINS：
    用户只关心「搜得到搜不到」，不值得为索引缺失报一个 500。
    """
    ids = _clean_kb_ids(kb_ids)
    kw = (keyword or "").strip()
    if not ids or not kw:
        return []
    params: dict[str, Any] = {"kb_ids": ids, "kw": kw, "limit": _clamp_limit(limit),
                              "type": entity_type or None}
    try:
        # 后缀加 *：全文索引按分词命中，整句直接查常常一个都不出
        rows = await neo4j_client.run(
            f"CALL db.index.fulltext.queryNodes('{FULLTEXT_INDEX}', $kw) YIELD node AS e, score "
            f"WHERE e.kb_id IN $kb_ids AND ($type IS NULL OR e.type = $type) "
            f"RETURN e.name AS name, e.type AS type, e.summary AS summary, e.kb_id AS kb_id, "
            f"       e.aliases AS aliases, score ORDER BY score DESC LIMIT $limit",
            {**params, "kw": f"{kw}*"})
    except Neo4jError as e:
        log.warning(f"实体全文索引检索失败，退化 CONTAINS: {e}")
        rows = []
    if rows:
        return [_entity_dto(r) for r in rows]
    # 兜底：老库没建全文索引、或全文分词没命中（索引缺失不值得报一个 500）
    rows = await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id IN $kb_ids "
        f"  AND ($type IS NULL OR e.type = $type) "
        f"  AND (toLower(e.name) CONTAINS toLower($kw) "
        f"       OR ANY(a IN coalesce(e.aliases, []) WHERE toLower(a) CONTAINS toLower($kw))) "
        f"RETURN e.name AS name, e.type AS type, e.summary AS summary, e.kb_id AS kb_id, "
        f"       e.aliases AS aliases, 0.0 AS score "
        f"ORDER BY size(coalesce(e.aliases, [])) DESC LIMIT $limit", params)
    return [_entity_dto(r) for r in rows]


async def entity_sources(kb_ids: Sequence[int], name: str, *,
                         entity_type: Optional[str] = None,
                         limit: int = 50) -> list[dict]:
    """实体溯源：这个实体出现在哪些切片/文档里（检索页「图谱关联到原文」用）。"""
    ids = _clean_kb_ids(kb_ids)
    if not ids or not (name or "").strip():
        return []
    rows = await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY})-[r:{RC.KG_REL_MENTIONED}]->(c:{RC.KG_LABEL_CHUNK}) "
        f"WHERE e.kb_id IN $kb_ids AND e.name = $name "
        f"  AND ($type IS NULL OR e.type = $type) "
        f"RETURN c.es_id AS es_id, c.kb_id AS kb_id, c.doc_id AS doc_id, "
        f"       c.chunk_index AS chunk_index, c.content AS content, c.title_path AS title_path "
        f"ORDER BY c.kb_id, c.doc_id, c.chunk_index LIMIT $limit",
        {"kb_ids": ids, "name": name.strip(), "type": entity_type,
         "limit": _clamp_limit(limit)})
    return [dict(r) for r in rows]


async def entities_by_docs(kb_ids: Sequence[int], doc_ids: Sequence[int], *,
                           limit: int = RC.KG_VIZ_DEFAULT_LIMIT) -> list[dict]:
    """按文档定位实体（图谱页「只看这几篇的子图」的起点集合）。

    实体节点本身不带 doc_id（同一个实体可以被多篇文档提到），只能顺着
    MENTIONED_IN -> Chunk 的边按切片的 doc_id 反查；被提次数多的排前面，
    起点选得好，后续展开跳数相同时图面信息量更大。
    """
    ids = _clean_kb_ids(kb_ids)
    docs = [int(d) for d in (doc_ids or []) if d]
    if not ids or not docs:
        return []
    rows = await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY})-[r:{RC.KG_REL_MENTIONED}]->(c:{RC.KG_LABEL_CHUNK}) "
        f"WHERE c.kb_id IN $kb_ids AND c.doc_id IN $doc_ids "
        f"WITH e, count(r) AS mentions ORDER BY mentions DESC LIMIT $limit "
        f"RETURN e.name AS name, e.type AS type, e.summary AS summary, "
        f"       e.kb_id AS kb_id, e.aliases AS aliases, mentions AS score",
        {"kb_ids": ids, "doc_ids": docs, "limit": _clamp_limit(limit)})
    return [_entity_dto(r) for r in rows]


async def subgraph(kb_ids: Sequence[int], *, centers: Optional[Sequence[str]] = None,
                   depth: int = 1, limit: int = RC.KG_VIZ_DEFAULT_LIMIT,
                   entity_type: Optional[str] = None) -> dict:
    """邻域展开：给中心实体集合，按 RELATED_TO 逐跳出节点与边（图谱可视化）。

    为什么不用变长路径 `*1..n` 一次查：
    - 路径长度不能是参数（Cypher 语法限制），拼字符串又要引入注入面；
    - 返回 Path 对象摊平成 nodes/edges 反而更绕。
    逐跳查询最多 3 次，每跳都带 kb_id 白名单，规模由 limit 硬卡住。

    ``entity_type`` 只收窄**起点**（没给 centers 时的度数 top、以及按名字定的中心）；
    展开出的邻居不按类型过滤——只留同类型实体的图会凭空断开关系链。
    """
    ids = _clean_kb_ids(kb_ids)
    if not ids:
        return {"nodes": [], "edges": [], "truncated": False}
    limit = _clamp_limit(limit)
    depth = max(1, min(int(depth or 1), MAX_DEPTH))
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    names = [str(c).strip() for c in (centers or []) if str(c).strip()]

    if names:
        rows = await _entities_by_names(ids, names, entity_type=entity_type)
    else:
        rows = await _top_entities(ids, limit, entity_type=entity_type)
    for row in rows:
        _add_node(nodes, row)

    truncated = False
    for _ in range(depth):
        # 每一跳都以「图上已有的全部实体」为起点：上一跳新进来的也该继续往外长
        names = [n["name"] for n in nodes.values()]
        if len(names) >= limit:
            truncated = True
            break
        rows = await _neighbors(ids, names, limit - len(names))
        grown = len(nodes)
        for r in rows:
            src = _add_node(nodes, {"name": r["source"], "type": r["source_type"],
                                    "summary": r["source_summary"], "kb_id": r["kb_id"]})
            dst = _add_node(nodes, {"name": r["target"], "type": r["target_type"],
                                    "summary": r["target_summary"], "kb_id": r["kb_id"]})
            if not src or not dst or len(nodes) > limit:
                # 端点为空 / 已越过规模上限：这条边不再入图，但如实标 truncated
                truncated = True
                continue
            # 去重键用**无序对**：_neighbors 是无向匹配 (a)-[r]-(b)，同一条关系在
            # 两端都进了起点集合时会回两行（正向/反向各匹配一次）。按有向键去重就等于把一
            # 条关系画成两条重影边，“关系 N”也把真实条数翻倍（14 条写成 28 条）。
            # 两行的 source/target 已由 _neighbors 按 startNode/endNode 取成真方向，所以
            # 这里保留先出现的那条不会保错方向（早先直接拿 a/b 当方向时，留下的正是假方向）。
            pair = (src["id"], dst["id"])
            edges.setdefault((min(pair), max(pair), r["relation"]),
                             {"source": src["id"], "target": dst["id"],
                              "relation": r["relation"], "kb_id": r["kb_id"],
                              "doc_ids": [d for d in (r.get("doc_ids") or []) if d]})
        if not rows or len(nodes) == grown:
            # 本跳一个新节点都没多出（全图已覆盖或在上限上），再绕一圈只会多一次往返
            break
    return {"nodes": list(nodes.values()), "edges": list(edges.values()),
            "truncated": truncated or len(nodes) > limit}


async def _entities_by_names(kb_ids: list[int], names: Sequence[str], *,
                             entity_type: Optional[str] = None) -> list[dict]:
    """按名字取实体（给定的中心点；同名不同类的两个都会回来，在图上就是两个节点）。"""
    if not names:
        return []
    return await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id IN $kb_ids AND e.name IN $names "
        f"  AND ($type IS NULL OR e.type = $type) "
        f"RETURN e.name AS name, e.type AS type, e.summary AS summary, "
        f"       e.kb_id AS kb_id, e.aliases AS aliases",
        {"kb_ids": kb_ids, "names": list(names), "type": entity_type or None})


async def _top_entities(kb_ids: list[int], limit: int, *,
                        entity_type: Optional[str] = None) -> list[dict]:
    """库里度数最高的一批实体（图谱页的默认起点与详情页的「核心实体」榜单同一个口径）。"""
    return await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id IN $kb_ids "
        f"  AND ($type IS NULL OR e.type = $type) "
        f"OPTIONAL MATCH (e)-[r:{RC.KG_REL_RELATION}]-() "
        f"RETURN e.name AS name, e.type AS type, e.summary AS summary, "
        f"       e.kb_id AS kb_id, e.aliases AS aliases, count(r) AS degree "
        f"ORDER BY degree DESC LIMIT $limit",
        {"kb_ids": kb_ids, "limit": _clamp_limit(limit), "type": entity_type or None})


async def _neighbors(kb_ids: list[int], names: Sequence[str], limit: int) -> list[dict]:
    """一跳邻居。

    匹配是无向的（`(a)-[r]-(b)`）：RELATED_TO 的两端都得能当起点，否则从 tail 词搜过去
    会断链，图上只剩半边。但**返回的 source/target 必须取边的真实方向**（startNode/endNode），
    不能拿 a/b 直接充当 —— `WHERE a.name IN $names` 已经把 a 钉死成搜索锚点，那样等于
    「谁被搜到谁是箭头起点」：搜「东华理工大学」回 东华→蒋泽华，搜「蒋泽华」又回 蒋泽华→东华，
    同一条边的箭头跟着搜索词跑，前端按 source→target 画出来的箭头自然反了（实测 kb=14 简历
    子图：库里 14 条边全部是 人物→单位/学校，用「东华理工大学」作关键词时那条就被翻成了反）。
    """
    if not names or limit <= 0:
        return []
    return await neo4j_client.run(
        f"MATCH (a:{RC.KG_LABEL_ENTITY})-[r:{RC.KG_REL_RELATION}]-(b:{RC.KG_LABEL_ENTITY}) "
        f"WHERE a.kb_id IN $kb_ids AND b.kb_id IN $kb_ids AND a.name IN $names "
        f"  AND a.name <> b.name "
        # 定完方向再出字段：h 恒为这条边真正的 head，与 a/b 谁是锚点无关
        f"WITH r, startNode(r) AS h, endNode(r) AS t "
        f"RETURN h.name AS source, h.type AS source_type, h.summary AS source_summary, "
        f"       t.name AS target, t.type AS target_type, t.summary AS target_summary, "
        f"       r.relation AS relation, r.kb_id AS kb_id, "
        f"       r.doc_ids AS doc_ids LIMIT $limit",
        {"kb_ids": kb_ids, "names": list(names), "limit": max(1, int(limit))})


def _add_node(nodes: dict[str, dict], row: dict) -> Optional[dict]:
    """节点入集合（按 kb_id+type+name 去重）；名字为空时给 None，让调用方标记截断。"""
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    kb_id = int(row.get("kb_id") or 0)
    node_id = _entity_key(name, str(row.get("type") or ""), kb_id)
    if node_id in nodes:
        return nodes[node_id]
    node = {"id": node_id, "name": name, "type": str(row.get("type") or DEFAULT_ENTITY_TYPE),
            "summary": str(row.get("summary") or ""), "kb_id": kb_id,
            "aliases": [str(a) for a in (row.get("aliases") or []) if a]}
    nodes[node_id] = node
    return node


async def statistics(kb_ids: Sequence[int], *, top: int = 10) -> dict:
    """规模统计（知识库详情页的图谱概览：实体数、关系数、类型分布、核心实体）。"""
    ids = _clean_kb_ids(kb_ids)
    if not ids:
        # 白名单为空 = 一个都不给看：统计全 0，绝不退化成全图计数
        return {"enabled": False, "entity_count": 0, "relation_count": 0, "chunk_count": 0,
                "document_count": 0, "types": [], "top_entities": []}
    params = {"kb_ids": ids}
    out: dict[str, Any] = {"enabled": True}
    for key, label in (("entity_count", RC.KG_LABEL_ENTITY),
                       ("chunk_count", RC.KG_LABEL_CHUNK),
                       ("document_count", RC.KG_LABEL_DOC)):
        out[key] = _count_of(await neo4j_client.run(
            f"MATCH (n:{label}) WHERE n.kb_id IN $kb_ids RETURN count(n) AS c", params))
    out["relation_count"] = _count_of(await neo4j_client.run(
        f"MATCH ()-[r:{RC.KG_REL_RELATION}]->() WHERE r.kb_id IN $kb_ids RETURN count(r) AS c",
        params))
    out["types"] = [{"type": r.get("type") or DEFAULT_ENTITY_TYPE,
                     "count": int(r.get("count") or 0)} for r in await neo4j_client.run(
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id IN $kb_ids "
        f"RETURN e.type AS type, count(*) AS count ORDER BY count DESC", params)]
    out["top_entities"] = [{"name": r.get("name"), "type": r.get("type"),
                            "kb_id": int(r.get("kb_id") or 0),
                            "degree": int(r.get("degree") or 0)}
                           for r in await _top_entities(ids, max(1, int(top)))]
    return out


def _count_of(rows: list[dict], key: str = "c") -> int:
    """取 count 查询里的那一个数（这类语句恒一行，没命中就是 0）。"""
    return int((rows[0].get(key) if rows else 0) or 0)


def _entity_dto(row: dict) -> dict:
    kb_id = int(row.get("kb_id") or 0)
    name = str(row.get("name") or "")
    etype = str(row.get("type") or DEFAULT_ENTITY_TYPE)
    return {"id": _entity_key(name, etype, kb_id), "name": name, "type": etype,
            "summary": str(row.get("summary") or ""), "kb_id": kb_id,
            "aliases": [str(a) for a in (row.get("aliases") or []) if a],
            "score": float(row.get("score") or 0.0)}


# =====================================================================================
# 四、清理（删除链路的图谱侧，与 ES/存储清理并列，调用方已完成权限判定）
# =====================================================================================
async def delete_by_doc(kb_id: int, doc_id: int, *, keep_document: bool = False) -> dict:
    """回收一篇文档贡献的图：摘溯源边 → 摘关系支撑 → 删本篇切片 → 清孤儿实体。

    顺序不能换：孤儿实体（「再没有 MENTIONED_IN 指向任何切片」）只能在本篇切片
    删掉之后判，否则只被这一篇提到的实体会永远留在图上；重解析一次文档就是多一份
    删不掉的幽灵关系。
    """
    params = {"kb_id": int(kb_id), "doc_id": int(doc_id)}
    steps = [
        f"MATCH ()-[r:{RC.KG_REL_MENTIONED}]->(c:{RC.KG_LABEL_CHUNK}) "
        f"WHERE c.kb_id = $kb_id AND c.doc_id = $doc_id DELETE r",
        # 支撑列表摘掉本篇：还有别的文档撑着就留着这条边
        f"MATCH ()-[r:{RC.KG_REL_RELATION}]->() WHERE r.kb_id = $kb_id "
        f"  AND $doc_id IN coalesce(r.doc_ids, []) "
        f"SET r.doc_ids = [d IN r.doc_ids WHERE d <> $doc_id]",
        f"MATCH ()-[r:{RC.KG_REL_RELATION}]->() WHERE r.kb_id = $kb_id "
        f"  AND coalesce(r.doc_ids, []) = [] DELETE r",
        f"MATCH (c:{RC.KG_LABEL_CHUNK}) WHERE c.kb_id = $kb_id AND c.doc_id = $doc_id "
        f"DETACH DELETE c",
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id = $kb_id "
        f"  AND NOT (e)-[:{RC.KG_REL_MENTIONED}]->() DETACH DELETE e",
    ]
    if not keep_document:
        steps.append(f"MATCH (d:{RC.KG_LABEL_DOC}) WHERE d.kb_id = $kb_id AND d.doc_id = $doc_id "
                     f"DETACH DELETE d")
    nodes = rels = 0
    for stmt in steps:
        res = await neo4j_client.write(stmt, params)
        nodes += res["nodes_deleted"]
        rels += res["rels_deleted"]
    out = {"deleted_nodes": nodes, "deleted_rels": rels}
    log.info(f"图谱已按文档清理: kb={kb_id} doc={doc_id} {out}")
    return out


async def delete_by_chunk(kb_id: int, doc_id: int, es_id: str) -> dict:
    """回收**一条切片**贡献的图：摘溯源边 → 删切片节点 → 清因此变成孤儿的实体。

    与 delete_by_doc 同一套「先摘边再判孤儿」的顺序，只把范围收到一条切片上：
    文档节点不动（删一片切片不等于删这篇文档），关系的 doc_ids 也不摘——
    支撑列表是**文档粒度**的，同一文档还剩其它切片在支持这条边时把它抽掉就是错删。
    """
    if not es_id:
        return {"deleted_nodes": 0, "deleted_rels": 0}
    params = {"kb_id": int(kb_id), "doc_id": int(doc_id), "es_id": str(es_id)}
    steps = [
        f"MATCH ()-[r:{RC.KG_REL_MENTIONED}]->(c:{RC.KG_LABEL_CHUNK}) "
        f"WHERE c.es_id = $es_id DELETE r",
        f"MATCH (c:{RC.KG_LABEL_CHUNK}) WHERE c.es_id = $es_id DETACH DELETE c",
        # 只被这条切片提到过的实体已经没有任何溯源，跟着回收（与整篇删除同口径）
        f"MATCH (e:{RC.KG_LABEL_ENTITY}) WHERE e.kb_id = $kb_id "
        f"  AND NOT (e)-[:{RC.KG_REL_MENTIONED}]->() DETACH DELETE e",
    ]
    nodes = rels = 0
    for stmt in steps:
        res = await neo4j_client.write(stmt, params)
        nodes += res["nodes_deleted"]
        rels += res["rels_deleted"]
    out = {"deleted_nodes": nodes, "deleted_rels": rels}
    log.info(f"图谱已按切片清理: kb={kb_id} doc={doc_id} es_id={es_id} {out}")
    return out


async def delete_by_kb(kb_id: int) -> dict:
    """整库图谱清理：三种标签的节点一起摘（关系由 DETACH 连带删除）。"""
    res = await neo4j_client.write(
        f"MATCH (n) WHERE n.kb_id = $kb_id AND "
        f"(n:{RC.KG_LABEL_DOC} OR n:{RC.KG_LABEL_CHUNK} OR n:{RC.KG_LABEL_ENTITY}) "
        f"DETACH DELETE n", {"kb_id": int(kb_id)})
    out = {"deleted_nodes": res["nodes_deleted"], "deleted_rels": res["rels_deleted"]}
    log.info(f"图谱已按知识库清理: kb={kb_id} {out}")
    return out


def _clamp_limit(value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return RC.KG_VIZ_DEFAULT_LIMIT
    return max(1, min(n, 1000))


__all__ = ["ensure_document", "upsert_graph", "search_entities", "entity_sources",
           "entities_by_docs", "subgraph", "statistics", "delete_by_doc", "delete_by_chunk",
           "delete_by_kb", "entity_type_of"]
