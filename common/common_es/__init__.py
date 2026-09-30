# -*- coding: utf-8 -*-
"""知识库检索存储（Elasticsearch）统一入口。

对外只暴露三样东西：
- es_client：全局异步单例（init/close/ensure_index/读写检索）
- init_es/close_es：服务与 worker 启动、退出钩子（读 Nacos 的 es 段）
- run_sync：无事件循环上下文时的同步退化执行

用法（FastAPI 服务在 lifespan 内、arq worker 在 bootstrap 内）：
    from common.common_es import init_es, es_client
    await init_es(yml.get("es", {}))          # 幂等，连接 + 建索引
    hits = await es_client.search_hybrid(kb_ids=[1,2], query_text="合同", query_vector=vec)
"""
from __future__ import annotations

from typing import Any, Optional

from common.common_es.client import (AsyncEsClient, CustomError, es_client,
                                     from_json, run_sync, to_json)
from common.common_es.index_mapping import INDEX_NAME, build_mapping

__all__ = [
    "AsyncEsClient", "CustomError", "es_client", "init_es", "close_es",
    "run_sync", "to_json", "from_json", "INDEX_NAME", "build_mapping",
]


async def init_es(es_cfg: dict | None = None, *, ensure: bool = True) -> bool:
    """初始化 ES 单例，并按需确认索引存在（幂等，返回本次是否新建了索引）。"""
    if not es_cfg:
        raise CustomError("缺少 es 配置段（Nacos service_rag.es / arq_ragflow.es）")
    await es_client.init(es_cfg)
    if ensure:
        return await es_client.ensure_index(int(es_cfg.get("vector_dim") or 0) or None)
    return False


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
    src["highlight"] = hit.get("highlight") or {}
    src["source"] = from_json(src.get("source"))
    return src
