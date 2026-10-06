# -*- coding: utf-8 -*-
"""知识图谱存储（Neo4j）统一入口（与 common_es 同一套用法）。

对外只暴露两样东西：
- neo4j_client：全局异步单例（init/close/run/write/ensure_schema）
- init_neo4j/close_neo4j：服务与 worker 启动、退出钩子（读 Nacos 的 neo4j 段）

图谱语句全部收在 kg_store（读写门面），业务层只见数据结构：
    from common.common_neo4j import init_neo4j, kg_store
    await init_neo4j(yml.get("neo4j", {}))        # 幂等，连接 + 建约束/索引
    await kg_store.upsert_graph(kb_id, doc_id, entities, relations)
"""
from __future__ import annotations

from common.common_neo4j import kg_store
from common.common_neo4j.client import (AsyncNeo4jClient, Neo4jError,
                                        neo4j_client)

__all__ = ["AsyncNeo4jClient", "Neo4jError", "neo4j_client", "kg_store",
           "init_neo4j", "close_neo4j"]


async def init_neo4j(neo4j_cfg: dict | None = None, *, ensure_schema: bool = True) -> None:
    """初始化 Neo4j 单例，并按需确认约束/索引存在（幂等）。

    neo4j 段必填项只有 url（user/password 不给就是不认证）。连不上直接抛 Neo4jError：
    与 ES 同口径——服务与 worker 都不做「起来了一个库都查不了」的半成品，
    业务侧要判可用性用 ``neo4j_client.ready``。
    """
    if not neo4j_cfg:
        raise Neo4jError("缺少 neo4j 配置段（Nacos service_rag.neo4j / arq_ragflow.neo4j）")
    await neo4j_client.init(neo4j_cfg)
    if ensure_schema:
        await neo4j_client.ensure_schema()


async def close_neo4j() -> None:
    """释放 Neo4j 连接（未初始化时静默返回，便于服务启动失败也能安全走 shutdown）。"""
    if neo4j_client.ready:
        await neo4j_client.close()
