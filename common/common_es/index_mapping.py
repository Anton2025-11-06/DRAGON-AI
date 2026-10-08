# -*- coding: utf-8 -*-
"""ES 单索引 `rag_knowledge_chunk` 的 mapping 定义与文档 _id 规则（SPEC §10.2）。

为什么只有一个索引
------------------
文本切片、图片、音视频全局向量**共用同一个多模态向量空间**（SPEC §10.1），跨模态检索本来就
是一次 kNN 的候选集里同时有文字和图，按类型拆索引反而要在应用层做结果合并与重排。因此：
- 索引级隔离靠 `kb_id`（所有检索强制前置 filter，白名单由上层 ACL 算好后传下来）；
- 模态区分靠 `chunk_type`（text/image/audio_video），检索侧按需加 term filter；
- 维度在建索引时固定 `dims=1024`，之后任何知识库选了别的维度的模型都写不进来（ES 直接报错），
  这正是 SPEC「向量模型创建后永久锁定」的底层保障，不需要应用再写一份校验。

字段命名与 common_constants.rag_constant 的 ES_FIELD_* 一一对应，业务代码不写字面量。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC

# 索引名：与常量同源（Nacos 的 es.index 只做同名校验，不允许指向另一个索引——
# 换索引等于换全部数据，靠配置热切只会写出两个索引各一半的脏状态）
INDEX_NAME = RC.RAG_CHUNK_INDEX

# 向量参数（SPEC §10.1 固定值，不可由配置覆盖）
DEFAULT_DIM = RC.RAG_VECTOR_DIM
VECTOR_SIMILARITY = "cosine"      # 余弦：与向量模型输出的归一化/embedding 常规口径一致
# index_options 只放「建索引期」合法的参数（type/m/ef_construction）：
# num_candidates 是**检索期**参数（在 client.vector_search 的 knn 体里给），写进 mapping 会被
# ES 直接拒（实测 ES 9.5.3：mapper_parsing_exception —— Mapping definition for [embedding]
# has unsupported parameters: [num_candidates : 100]），后果是索引建不出来、服务起不来。
VECTOR_INDEX_OPTIONS = {"type": "hnsw", "ef_construction": 100}

# 分片/副本：单机默认 1 分片 0 副本（建索引后分片数不可改，多节点部署时按数据量在配置里给）
DEFAULT_SHARDS = 1
DEFAULT_REPLICAS = 0

# content 的 keyword 子字段只用于精确匹配小段文本（长文本超过该长度不进 keyword，避免超限报错）
CONTENT_KEYWORD_IGNORE_ABOVE = 256

DATE_FORMAT = "epoch_millis||strict_date_optional_time||yyyy-MM-dd HH:mm:ss"


def field_specs(dim: int = DEFAULT_DIM, *, analyzer: Optional[str] = None) -> dict[str, Any]:
    """mapping 的 properties 部分（单独成函数：建索引与「校验已有索引是否缺字段」两处都要用）。

    :param analyzer: content 的分词器；留空用 ES 默认（standard）。装了 ik 插件时在
        Nacos 的 es.analyzer 填 ik_max_word，检索分析器同值即可，代码不做插件探测。
    """
    text_type = {"type": "text", "fields": {"keyword": {
        "type": "keyword", "ignore_above": CONTENT_KEYWORD_IGNORE_ABOVE}}}
    if analyzer:
        text_type = {"type": "text", "analyzer": analyzer, "search_analyzer": analyzer,
                     "fields": text_type["fields"]}
    return {
        # ---------- 隔离与溯源 ----------
        RC.ES_FIELD_ORG: {"type": "long"},
        RC.ES_FIELD_KB: {"type": "long"},
        RC.ES_FIELD_DOC: {"type": "long"},
        RC.ES_FIELD_CHUNK: {"type": "long"},
        RC.ES_FIELD_INDEX: {"type": "integer"},
        # ---------- 模态与内容 ----------
        RC.ES_FIELD_TYPE: {"type": "keyword"},
        RC.ES_FIELD_CONTENT: text_type,
        RC.ES_FIELD_EMBED: {
            "type": "dense_vector", "dims": int(dim), "index": True,
            "similarity": VECTOR_SIMILARITY, "index_options": dict(VECTOR_INDEX_OPTIONS),
        },
        # 媒体地址与展示用元信息：只存不索引（media_url 是 OSS URL，从不作为检索条件）
        RC.ES_FIELD_MEDIA_URL: {"type": "keyword", "index": False, "doc_values": False},
        RC.ES_FIELD_TITLE_PATH: {"type": "keyword"},
        RC.ES_FIELD_PAGE: {"type": "integer"},
        RC.ES_FIELD_SHEET: {"type": "keyword"},
        # ---------- 状态位 ----------
        # available：文档被停用/单块被屏蔽时置 false，检索默认过滤掉（不删数据，可随时放开）
        RC.ES_FIELD_AVAILABLE: {"type": "boolean"},
        RC.ES_FIELD_DELETED: {"type": "byte"},
        RC.ES_FIELD_CREATE_TIME: {"type": "date", "format": DATE_FORMAT},
        # 扩展信息落一个 JSON 串（章节序号、图片 block_id、媒体时长等），
        # dynamic=strict 下新字段一律进这里，不悄悄扩表结构
        RC.ES_FIELD_SOURCE: {"type": "keyword", "index": False, "doc_values": False},
    }


def build_mapping(dim: Optional[int] = None, *, shards: Optional[int] = None,
                  replicas: Optional[int] = None,
                  analyzer: Optional[str] = None) -> dict[str, Any]:
    """建索引请求体：`{"settings": ..., "mappings": ...}`（client.ensure_index 直接用）。"""
    return {
        "settings": {
            "index.number_of_shards": int(shards if shards is not None else DEFAULT_SHARDS),
            "index.number_of_replicas": int(replicas if replicas is not None else DEFAULT_REPLICAS),
            # 检索一次拉多路候选，刷新频率不必很低；这里不改 refresh_interval，保持 ES 默认 1s，
            # 解析完到能搜出来的延迟就落在用户预期内（页面写完就查会立刻有结果）
        },
        "mappings": {
            # strict：写进未声明字段直接报错。宁可让解析任务失败在第一条脏数据上，
            # 也不要静默丢弃字段后「向量存在但溯源信息没了」这种查不出来的问题
            "dynamic": "strict",
            "properties": field_specs(dim or DEFAULT_DIM, analyzer=analyzer),
        },
    }


# ==================== 文档 _id 规则（幂等写入的唯一依据） ====================
def es_doc_id(kb_id: int, doc_id: int, chunk_index: int) -> str:
    """`{kb_id}_{doc_id}_{chunk_index}`：与 tb_document_chunk 的 uk_doc_chunk 一一对应。

    重跑同一文档时按 _id 覆盖同名切片，不会出现「重跑一次分块数翻倍」；
    带 kb_id 前缀则保证不同知识库的切片 id 天然不撞，删库时也能按前缀批量清理。
    """
    return RC.ES_ID_SEP.join([str(int(kb_id)), str(int(doc_id)), str(int(chunk_index))])


def parse_es_doc_id(es_id: str) -> tuple[int, int, int]:
    """_id 反解 (kb_id, doc_id, chunk_index)；格式不符抛 ValueError（调用方按脏数据处理）。"""
    parts = str(es_id or "").split(RC.ES_ID_SEP)
    if len(parts) != 3:
        raise ValueError(f"非法的切片 _id: {es_id}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def es_id_prefix(kb_id: Optional[int] = None, doc_id: Optional[int] = None) -> str:
    """按库/文档拼 _id 前缀（配合 delete_by_query 的 ids 集或 prefix query 清理）。"""
    segs: list[str] = []
    if kb_id is not None:
        segs.append(str(int(kb_id)))
    if doc_id is not None:
        segs.append(str(int(doc_id)))
    return RC.ES_ID_SEP.join(segs)


def check_dim(dim: int) -> int:
    """校验向量维度：非 1024 直接拒（SPEC §10.1 固定维度，不留配置口子）。"""
    dim = int(dim or 0)
    if dim != DEFAULT_DIM:
        raise ValueError(f"向量维度必须是 {DEFAULT_DIM}（当前 {dim or '未取到'}）："
                         f"换维度等于换向量空间，历史切片全部作废")
    return dim


def mapping_field_names() -> Sequence[str]:
    """全部字段名（排查「索引里有没有这个字段」时打日志用）。"""
    return tuple(field_specs().keys())
