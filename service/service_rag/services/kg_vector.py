# -*- coding: utf-8 -*-
"""图谱向量投影（需求 9a）：Neo4j 的实体/关系 → ES 索引 `rag_kg_vector` 的增量同步。

投影不是双写事务
----------------
图谱的真身在 Neo4j，这里只是它的**向量投影**：可随时按整库重算，不落 MySQL 表、
不参与事务。所以本模块的唯一口径是「以 Neo4j 当前状态为准，把差集补上、把残留删掉」，
而不是「抽取成功时在两个存储各写一遍」。两次调用之间 Neo4j 变了，下一次同步就收敛过去。

为什么每次构建后按整库算，却不会每篇都重烧一遍向量模型
------------------------------------------------------
实体是**库内同名同类合并**的：B 文档重建会改写到 A 文档提过的实体摘要（`ON MATCH SET
e.summary = coalesce(row.summary, e.summary)`），只投影本篇结果会让那些实体的向量停在旧描述上。
所以源数据一律整库拉，但只对「文本变了或新增」的那几条调向量模型——文本没变的不重算，
Neo4j 里已不存在的项目当场删掉。代价是一次 `_source` 只取 text 的 scan（不拉向量）。

向量空间与切片同源
------------------
用知识库自己的 `embedding_model_id` 编码：与切片同一模型、同一 1024 维、同一 cosine，
所以检索侧一次问句编码既能查切片也能查实体/关系，不需要第二个向量模型。
这也意味着**换向量模型等于全部投影作废**——知识库侧向量模型创建后永久锁定，这里不用管。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_es import es_client, to_json
from common.common_es.kg_index_mapping import (kg_entity_key, kg_relation_key, kg_vector_id,
                                               truncate_text)
from common.common_log.log_init import log
from common.common_neo4j import kg_store


def entity_vector_text(name: str, etype: str, summary: str = "",
                       aliases: Optional[list[str]] = None) -> str:
    """实体的可检索文本：名字打头 + 类型 + 摘要 + 别名。

    名字必须在最前：问句里用户说的就是名字，让「东华理工大学」这种长实体名与
    问句向量对得上，靠的是名字而不是那句摘要。别名一起进去，用户喊「华工」也能
    命中登记成「华南理工大学」的实体（LightRAG 的 low-level search 同样靠这个名字面）。
    """
    parts = [str(name or "").strip()]
    etype = str(etype or "").strip()
    if etype:
        parts.append(f"类型：{etype}")
    summary = str(summary or "").strip()
    if summary:
        parts.append(summary)
    alts = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    if alts:
        parts.append("别名：" + "、".join(alts))
    return truncate_text(" | ".join(p for p in parts if p))


def relation_vector_text(head: str, relation: str, tail: str) -> str:
    """关系的可检索文本：三元组原样成句（Neo4j 的边不存描述，能用的就这三段）。"""
    head = str(head or "").strip()
    rel = str(relation or "").strip()
    tail = str(tail or "").strip()
    return truncate_text(f"{head} {rel} {tail}")


def _desired_docs(kb_id: int, rows: dict, *, org_id: int = 0) -> dict[str, dict]:
    """Neo4j 源数据 → `{投影 _id: 文档体（不含向量）}`，文本同时是差集比对的依据。"""
    out: dict[str, dict] = {}
    for one in rows.get("entities") or []:
        name = str(one.get("name") or "").strip()
        if not name:
            continue
        etype = kg_store.entity_type_of(one.get("type"))
        text = entity_vector_text(name, etype, str(one.get("summary") or ""),
                                  [str(a) for a in (one.get("aliases") or []) if a])
        out[kg_vector_id(kb_id, RC.KG_VECTOR_KIND_ENTITY, kg_entity_key(name, etype))] = {
            RC.KG_FIELD_KIND: RC.KG_VECTOR_KIND_ENTITY,
            RC.KG_FIELD_NAME: name,
            RC.KG_FIELD_ETYPE: etype,
            RC.KG_FIELD_TEXT: text,
            RC.KG_FIELD_REFS: to_json({"name": name, "type": etype,
                                       "summary": str(one.get("summary") or ""),
                                       "aliases": [str(a) for a in (one.get("aliases") or [])
                                                   if a]}),
        }
    for one in rows.get("relations") or []:
        head = str(one.get("head") or "").strip()
        tail = str(one.get("tail") or "").strip()
        rel = str(one.get("relation") or "").strip()
        if not head or not tail or not rel:
            continue
        out[kg_vector_id(kb_id, RC.KG_VECTOR_KIND_RELATION,
                         kg_relation_key(head, rel, tail))] = {
            RC.KG_FIELD_KIND: RC.KG_VECTOR_KIND_RELATION,
            RC.KG_FIELD_NAME: f"{head} {rel} {tail}"[:200],
            RC.KG_FIELD_ETYPE: rel[:100],
            RC.KG_FIELD_TEXT: relation_vector_text(head, rel, tail),
            RC.KG_FIELD_REFS: to_json({"head": head, "relation": rel, "tail": tail,
                                       "head_type": kg_store.entity_type_of(
                                           one.get("head_type")),
                                       "tail_type": kg_store.entity_type_of(
                                           one.get("tail_type"))}),
        }
    for doc in out.values():
        doc[RC.KG_FIELD_KB] = int(kb_id)
        doc[RC.KG_FIELD_ORG] = int(org_id or 0)
    return out


async def sync_kb_vectors(kb_id: int, *, embed_model_id: int, org_id: int = 0,
                         embed_config: Any = None) -> dict:
    """按 Neo4j 当前状态补齐一个库的实体/关系向量投影（幂等，可反复调）。

    :param embed_config: 已经取好的向量模型配置（构建链路里传进来，省一次查库）；
        没传就按 embed_model_id 现取。取不到模型时报 ValueError——投影写不出向量
        比不写更糟（一堆零向量条目会让 kNN 随机命中）。
    :return: {"entities","relations","embedded","deleted","truncated"}
    """
    from service.service_rag.services.rag_model import RagModelService

    kb_id = int(kb_id)
    rows = await kg_store.vector_rows(kb_id)
    desired = _desired_docs(kb_id, rows, org_id=int(org_id or 0))
    existing = await es_client.kg_scan_texts(kb_id, size=RC.KG_VECTOR_SCAN_LIMIT)

    changed = {kg_id: doc for kg_id, doc in desired.items()
               if existing.get(kg_id) != doc[RC.KG_FIELD_TEXT]}
    stale = [kg_id for kg_id in existing if kg_id not in desired]
    ent_total = sum(1 for d in desired.values()
                    if d[RC.KG_FIELD_KIND] == RC.KG_VECTOR_KIND_ENTITY)

    docs: list[dict] = []
    if changed:
        cfg = embed_config
        if cfg is None:
            cfg = await RagModelService.require_config(embed_model_id, "向量模型")
        items = list(changed.items())
        vectors = await RagModelService.embed_texts(cfg, [d[RC.KG_FIELD_TEXT]
                                                          for _, d in items])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for (kg_id, meta), vec in zip(items, vectors):
            if not vec or all(abs(float(x)) < 1e-12 for x in vec):
                # 零向量 = 这一条没编出来（模型返回缺位），写进去只会让 kNN 随机命中
                log.warning(f"图谱向量编码为空，跳过写入: kb={kb_id} "
                            f"id={meta.get(RC.KG_FIELD_NAME)}")
                continue
            docs.append({"_id": kg_id, **meta, RC.KG_FIELD_EMBED: [float(x) for x in vec],
                         RC.KG_FIELD_CREATE_TIME: now})
        await es_client.kg_put(docs)

    deleted = 0
    if stale:
        deleted = await es_client.kg_delete_by_ids(stale)

    out = {"entities": ent_total, "relations": len(desired) - ent_total,
           "embedded": len(docs), "deleted": deleted,
           "truncated": bool(rows.get("truncated"))}
    log.info("图谱向量投影已同步: kb={} 实体={} 关系={} 本次编码={} 清理={}",
             kb_id, out["entities"], out["relations"], out["embedded"], out["deleted"])
    return out


async def forget_kb(kb_id: int) -> int:
    """删库时的投影清理（与 ES 切片清理、Neo4j 图清理并列，调用方已完成权限判定）。"""
    return await es_client.kg_delete_by_kb(kb_id)


__all__ = ["sync_kb_vectors", "forget_kb", "entity_vector_text", "relation_vector_text"]
