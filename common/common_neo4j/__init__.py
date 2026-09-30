# -*- coding: utf-8 -*-
"""知识图谱存储（Neo4j）统一入口。

对外暴露：
- neo4j_client：全局异步单例（init/close/run/ensure_schema/health）
- init_neo4j/close_neo4j：服务与 worker 的启动、退出钩子（读 Nacos 的 neo4j 段）
- kg_store：图谱读写门面（upsert_graph/search_entities/subgraph/statistics/delete_by_*）
- run_sync：无事件循环上下文时的同步退化执行

用法：
    from common.common_neo4j import init_neo4j, kg_store
    await init_neo4j(yml.get("neo4j", {}))
    await kg_store.upsert_graph(kb_id, doc_id, entities, relations)
"""
from __future__ import annotations

from common.common_neo4j import kg_store
from common.common_neo4j.client import (AsyncNeo4jClient, Neo4jError,
                                        neo4j_client, run_sync)

__all__ = ["AsyncNeo4jClient", "Neo4jError", "neo4j_client", "kg_store",
           "init_neo4j", "close_neo4j", "neo4j_health", "run_sync"]


async def init_neo4j(neo4j_cfg: dict | None = None, *, ensure_schema: bool = True) -> None:
    """初始化 Neo4j 异步驱动，并按需建好约束/索引（幂等）。

    图谱是 doc 型知识库的可选增强：连接失败不阻断服务启动（文档检索仍可用），
    只把降级结果打进日志与 health，由图谱构建接口在调用时再明确报错。
    """
    if not neo4j_cfg:
        raise Neo4jError("缺少 neo4j 配置段（Nacos service_rag.neo4j / arq_ragflow.neo4j）")
    await neo4j_client.init(neo4j_cfg)
    if ensure_schema:
        await neo4j_client.ensure_schema()


async def close_neo4j() -> None:
    if neo4j_client.ready:
        await neo4j_client.close()


async def neo4j_health() -> dict:
    if not neo4j_client.ready:
        return {"ok": False, "error": "Neo4j 未初始化（图谱功能不可用）"}
    return await neo4j_client.health()
