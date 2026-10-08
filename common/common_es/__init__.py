# -*- coding: utf-8 -*-
"""知识库检索存储（Elasticsearch）统一入口。

对外只暴露两样东西：
- es_client：全局异步单例（init/close/ensure_index/读写检索）
- init_es/close_es：服务与 worker 启动、退出钩子（读 Nacos 的 es 段）

用法（FastAPI 服务在 lifespan 内、arq worker 在 bootstrap 内）：
    from common.common_es import init_es, es_client
    await init_es(yml.get("es", {}))          # 幂等，连接 + 建索引
    hits = await es_client.search_hybrid(kb_ids=[1,2], query_text="合同", query_vector=vec)
"""
from __future__ import annotations

from typing import Any, Optional

from common.common_es.client import (AsyncEsClient, CustomError, es_client,
                                     from_json, to_json)
from common.common_es.index_mapping import INDEX_NAME, build_mapping
from common.common_es.kg_index_mapping import KG_INDEX_NAME, build_kg_mapping
from common.common_log.log_init import log

__all__ = [
    "AsyncEsClient", "CustomError", "es_client", "init_es", "close_es", "es_health",
    "current_index", "chunk_source", "to_json", "from_json", "INDEX_NAME", "build_mapping",
    "KG_INDEX_NAME", "build_kg_mapping", "kg_source",
]


async def init_es(es_cfg: dict | None = None, *, ensure: bool = True) -> bool:
    """初始化 ES 单例，并按需确认索引存在（幂等，返回本次是否新建了索引）。

    es 段必填项只有 url；认证没填就不认证（内网未开 security 的集群）。
    ensure 时一并建 rag_kg_vector：它是 Neo4j 的向量投影，没建过图谱时就是空索引，
    但启动就建好比「用户第一次开图谱增强才发现没索引」便宜得多。
    """
    if not es_cfg:
        raise CustomError("缺少 es 配置段（Nacos service_rag.es / arq_ragflow.es）")
    await es_client.init(es_cfg)
    # 建索引不再从配置取维度：dims 在建索引时固定 1024（与 rag_constant.RAG_VECTOR_DIM 同源），
    # 配置里再给一个 vector_dim 只会和已建索引打架，且报的是 ES 写不进去而不是启动错
    if not ensure:
        return False
    created = await es_client.ensure_index()
    # 图谱投影索引失败不阻断启动：切片检索是主链路，不该被可选件拖死
    try:
        created = bool(await es_client.ensure_kg_index()) or created
    except Exception as e:  # noqa: BLE001  可选件建不出来只降级图谱增强，不拦启动
        log.warning(f"图谱向量索引初始化失败（图谱增强检索不可用，切片检索不受影响）: {e}")
    return created


async def close_es() -> None:
    """释放 ES 连接（未初始化时静默返回，便于服务启动失败也能安全走 shutdown）。"""
    if es_client.ready:
        await es_client.close()


async def es_health() -> dict:
    """健康探测（未初始化时返回明确状态，供 /health 类接口聚合展示）。"""
    if not es_client.ready:
        return {"ok": False, "error": "ES 未初始化"}
    return await es_client.health()


def current_index() -> Optional[str]:
    return INDEX_NAME if es_client.ready else None


def chunk_source(hit: dict) -> dict[str, Any]:
    """把检索命中（{es_id, score, _source...}）摊平成业务 dict，统一在此收敛字段名。"""
    src = dict(hit.get("_source") or {})
    src["es_id"] = hit.get("es_id")
    src["score"] = hit.get("score")
    src["vector_score"] = hit.get("vector_score")
    src["keyword_score"] = hit.get("keyword_score")
    src["source"] = from_json(src.get("source"))
    return src


def kg_source(hit: dict) -> dict[str, Any]:
    """把图谱向量命中摊平成业务 dict（refs 从 JSON 串解回 dict，与 chunk_source 同构）。"""
    src = dict(hit.get("_source") or {})
    src["kg_id"] = hit.get("es_id")
    src["score"] = hit.get("score")
    src["refs"] = from_json(src.get("refs"))
    return src
