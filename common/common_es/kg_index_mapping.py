# -*- coding: utf-8 -*-
"""ES 索引 `rag_kg_vector` 的 mapping 与文档 _id 规则（需求 9：LightRAG 式图谱检索）。

为什么要单独一个索引
--------------------
切片索引 `rag_knowledge_chunk` 里每一条都是「一段正文」，检索的 size / score_threshold /
chunk_types 语义都是按段落定的。实体与关系的可检索文本是「名字 + 类型 + 摘要」和
「头 - 关系 - 尾」这种三元组，形态完全不同：混在一个索引里，文档库检索会捞出一堆
实体名当命中，图谱检索又受段落阈值摆布。所以分家：

- 一个库的实体/关系向量投影都落在这里，靠 `kb_id` 做索引级隔离（与切片索引同口径）；
- `kind` 区分两路（entity / relation），图谱检索按 kind 各做一次 kNN；
- 真身在 Neo4j，这里**只是它的向量投影**：可以随时整库重建，不建 MySQL 表、不做双写事务，
  投影与 Neo4j 不一致时以 Neo4j 为准重建（client.prune 链路）。

向量空间沿用切片索引的那一套（同一 embedding 模型、同 1024 维、同 cosine），
这样问句向量一次编码既能查切片也能查实体/关系，不需要第二个模型。
字段命名与 common_constants.rag_constant 的 KG_FIELD_* 一一对应，业务代码不写字面量。
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_es.index_mapping import (DEFAULT_DIM, DEFAULT_REPLICAS, DEFAULT_SHARDS,
                                            DATE_FORMAT, VECTOR_INDEX_OPTIONS,
                                            VECTOR_SIMILARITY, check_dim)

# 索引名：与常量同源（Nacos 的 es.kg_index 只做覆盖，用于灰度/重建新投影）
KG_INDEX_NAME = RC.RAG_KG_INDEX

# text 字段是分词检索用的可展示文本，同时是向量文本的落点（写进来是为了排障时能看见
# 「这条向量到底拿什么文本编码的」，不参与 BM25 融合）
TEXT_PREVIEW_CHARS = 2000


def kg_field_specs(dim: int = DEFAULT_DIM, *, analyzer: Optional[str] = None) -> dict[str, Any]:
    """mapping 的 properties 部分（与 index_mapping.field_specs 同构，字段集是它的子集）。"""
    text_type = {"type": "text"}
    if analyzer:
        text_type = {"type": "text", "analyzer": analyzer, "search_analyzer": analyzer}
    return {
        # ---------- 隔离与分类 ----------
        RC.KG_FIELD_ORG: {"type": "long"},
        RC.KG_FIELD_KB: {"type": "long"},
        RC.KG_FIELD_KIND: {"type": "keyword"},
        # name 双类型：keyword 供「按实体名精确回查」，text 供排障时全文找名字
        RC.KG_FIELD_NAME: {"type": "keyword", "fields": {"text": text_type}},
        RC.KG_FIELD_ETYPE: {"type": "keyword"},
        # ---------- 向量 ----------
        RC.KG_FIELD_TEXT: {"type": "text", "index": False},
        RC.KG_FIELD_EMBED: {
            "type": "dense_vector", "dims": int(dim), "index": True,
            "similarity": VECTOR_SIMILARITY, "index_options": dict(VECTOR_INDEX_OPTIONS),
        },
        # 回 Neo4j 反查溯源所需的键（JSON 串，只存不索引）：
        #   entity   → {"name","type","summary","aliases"}
        #   relation → {"head","relation","tail","head_type","tail_type"}
        RC.KG_FIELD_REFS: {"type": "keyword", "index": False, "doc_values": False},
        RC.KG_FIELD_CREATE_TIME: {"type": "date", "format": DATE_FORMAT},
    }


def build_kg_mapping(dim: Optional[int] = None, *, shards: Optional[int] = None,
                     replicas: Optional[int] = None,
                     analyzer: Optional[str] = None) -> dict[str, Any]:
    """建索引请求体：`{"settings": ..., "mappings": ...}`（client.ensure_kg_index 直接用）。"""
    return {
        "settings": {
            "index.number_of_shards": int(shards if shards is not None else DEFAULT_SHARDS),
            "index.number_of_replicas": int(replicas if replicas is not None else DEFAULT_REPLICAS),
        },
        "mappings": {
            # strict 的理由与切片索引一致：宁可写失败在第一条脏数据上，
            # 也不要静默丢字段后「向量在但溯源没了」
            "dynamic": "strict",
            "properties": kg_field_specs(dim or DEFAULT_DIM, analyzer=analyzer),
        },
    }


# ==================== 文档 _id 规则（幂等重建的唯一依据） ====================
def kg_vector_id(kb_id: int, kind: str, key: str) -> str:
    """`{kb_id}_{kind}_{md5(key)[:16]}`。

    key 由调用方按「实体名|类型」「头|关系|尾」拼好：md5 是为了让 _id 定长且不含
    冒号/斜杠这类会出现在实体名里的字符（_id 里有分隔符会被前缀清理误伤）。
    同一实体重建时按 _id 覆盖，不会出现「重跑一次图谱，向量翻倍」。
    """
    digest = hashlib.md5(str(key or "").encode("utf-8")).hexdigest()[:16]
    return RC.KG_VECTOR_ID_SEP.join([str(int(kb_id)), str(kind or "").strip(), digest])


def kg_entity_key(name: str, etype: str) -> str:
    """实体的幂等键：与 Neo4j 的 MERGE 键 (kb_id, name, type) 去掉 kb_id 的部分。"""
    return RC.KG_VECTOR_ID_SEP.join([str(name or "").strip(), str(etype or "").strip()])


def kg_relation_key(head: str, relation: str, tail: str) -> str:
    """关系的幂等键：方向是 Neo4j 边的真实方向，反向即视为另一条关系。"""
    return RC.KG_VECTOR_ID_SEP.join([str(head or "").strip(), str(relation or "").strip(),
                                     str(tail or "").strip()])


def parse_kg_vector_id(es_id: str) -> tuple[int, str, str]:
    """_id 反解 (kb_id, kind, md5)；格式不符抛 ValueError（调用方按脏数据处理）。"""
    parts = str(es_id or "").split(RC.KG_VECTOR_ID_SEP)
    if len(parts) != 3:
        raise ValueError(f"非法的图谱向量 _id: {es_id}")
    return int(parts[0]), parts[1], parts[2]


def truncate_text(value: Any) -> str:
    """向量文本截断（写进 text 字段只为排障可见，长摘要没必要整份存）。"""
    return str(value or "").strip()[:TEXT_PREVIEW_CHARS]


__all__ = ["KG_INDEX_NAME", "kg_field_specs", "build_kg_mapping", "kg_vector_id",
           "kg_entity_key", "kg_relation_key", "parse_kg_vector_id", "truncate_text",
           "check_dim", "DEFAULT_DIM", "DEFAULT_SHARDS", "DEFAULT_REPLICAS"]
