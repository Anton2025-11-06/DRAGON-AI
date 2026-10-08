# -*- coding: utf-8 -*-
"""Neo4j 全局异步单例客户端：连接、约束/索引、Cypher 执行（SPEC §10.3）。

设计约束（与 common_es.client 同一套口径，改代码前先对齐）
----------------------------------------------------------
1. **全局单例 + 纯异步**：driver 自带连接池，进程内只建一次（FastAPI lifespan 与
   arq worker bootstrap 各自调 ``init_neo4j``），任何方法都是协程，绝不在请求线程里
   做同步等待。
2. **会话用完即还**：每条 Cypher 开一个短会话、结果一次取回，不把游标交给调用方——
   高并发下挂着一个没消费完的会话就是挂着一个没还的连接。
3. **底层裸查**：本模块没有任何权限概念，只认调用方传下来的 ``kb_ids`` 白名单
   （上层 ACL 算好传下来，SPEC §13.4）；「白名单为空 = 一个都不给查」这条兜底落在
   kg_store 的每个读口上，与 ES 一致。
4. **语句只写在 kg_store**：业务层不见 Cypher，本文件只负责「取连接 → 跑语句 →
   把结果摊平成 dict」。
5. **连不上就报错**：图谱是 doc 型知识库的可选增强，但服务与 worker 启动都要求连上
   （与 ES 同口径：不做「起来了一个库都查不了」的服务）；运行期用 ``ready`` 判可用性。

Cypher 的写法口径
------------------
标签、关系类型、索引名一律来自 rag_constant 与本文件常量（f-string 拼进语句），
值一律走 ``$params``：拼值既是注入面，也会让查询计划失效。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_log.log_init import log

# 驱动可缺：与 common_es 同理，workflow / login 等服务不需要 Neo4j，没装包时本模块仍要
# 能 import（只在真要用时报一句「pip install neo4j」，而不是把整个 service_rag 拖垮）。
try:
    from neo4j import AsyncGraphDatabase

    HAS_NEO4J = True
    INSTALL_HINT = ""
except ImportError as e:  # noqa: BLE001
    AsyncGraphDatabase = None

    HAS_NEO4J = False
    INSTALL_HINT = f'未安装 neo4j 驱动（{e}）：pip install "neo4j>=5.14"'

# 连接池与超时：口径只有一套——没填用这里的默认，填了但不是数字也落回默认
DEFAULT_POOL_SIZE = 50
DEFAULT_TIMEOUT = 30.0
# 实体名全文索引名：建索引（本文件）与检索（kg_store 的 queryNodes）共用这一个名字，
# 两处各写一份字面量迟早会漂一个，漂了检索就静默退化 CONTAINS
FULLTEXT_INDEX = "rag_entity_name"

# 约束与索引：全部 IF NOT EXISTS，反复执行不报错（社区版无多库，database 留默认）
SCHEMA_STATEMENTS: tuple[str, ...] = (
    # 文档节点：一个知识库内的 doc_id 唯一
    f"CREATE CONSTRAINT rag_doc_key IF NOT EXISTS "
    f"FOR (d:{RC.KG_LABEL_DOC}) REQUIRE (d.kb_id, d.doc_id) IS UNIQUE",
    # 切片节点：用 ES 的 _id 作全局唯一键（{kb}_{doc}_{index}），与向量库一一对应
    f"CREATE CONSTRAINT rag_chunk_key IF NOT EXISTS "
    f"FOR (c:{RC.KG_LABEL_CHUNK}) REQUIRE (c.es_id) IS UNIQUE",
    # 实体节点：同名同类在同库内合并（跨库不合并，避免不同组织的资料串味）
    f"CREATE CONSTRAINT rag_entity_key IF NOT EXISTS "
    f"FOR (e:{RC.KG_LABEL_ENTITY}) REQUIRE (e.kb_id, e.name, e.type) IS UNIQUE",
    # 白名单过滤的主键：三个标签都按 kb_id 建范围索引
    f"CREATE INDEX rag_doc_kb IF NOT EXISTS FOR (d:{RC.KG_LABEL_DOC}) ON (d.kb_id)",
    f"CREATE INDEX rag_chunk_kb IF NOT EXISTS FOR (c:{RC.KG_LABEL_CHUNK}) ON (c.kb_id)",
    f"CREATE INDEX rag_entity_kb IF NOT EXISTS FOR (e:{RC.KG_LABEL_ENTITY}) ON (e.kb_id)",
    f"CREATE INDEX rag_entity_type IF NOT EXISTS FOR (e:{RC.KG_LABEL_ENTITY}) ON (e.type)",
    # 实体名全文索引：搜索框输入中文/英文都能召回（含别名属性）
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    f"FOR (e:{RC.KG_LABEL_ENTITY}) ON EACH [e.name, e.aliases]",
)


class Neo4jError(Exception):
    """图谱层的可读错误（未初始化/依赖缺失/Cypher 失败），交给统一异常处理器透出。"""


# ==================== 配置读取（Nacos 的 neo4j: 段） ====================
# 与 common_es 同一口径：必填只有 url，剩下的键没填（YAML 里注释掉根本不存在、留空是 ""）
# 都当「没填」走本文件的默认；配置写错不该变成一个看不懂的启动异常。
# 加密与否由 URI scheme 决定（bolt 明文 / neo4j+s 加密校验 / neo4j+ssc 自签），
# 代码不再递 encrypted/verify_connection：6.x 驱动已无 verify_connection 键，递了直接报错。

def _text(cfg: dict, *keys: str) -> str:
    """按顺序取第一个有值的字符串（已去首尾空白）；都没给返回空串。"""
    for key in keys:
        value = cfg.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _num(cfg: dict, *keys: str, default: float = 0) -> float:
    """按顺序取第一个能解析成数字的键；都取不到就用默认。"""
    for key in keys:
        try:
            return float(str(cfg.get(key)).strip())
        except (TypeError, ValueError):
            continue
    return float(default)


# ==================== 客户端 ====================
class AsyncNeo4jClient:
    """Neo4j 异步单例：进程内一个 driver，会话按需短取（连接池由 driver 自己管）。"""

    def __init__(self) -> None:
        self._driver: Any = None
        self._cfg: dict[str, Any] = {}
        self._database: str = ""
        self._loop_id: Optional[int] = None

    # ---------- 生命周期 ----------
    @property
    def ready(self) -> bool:
        return self._driver is not None

    async def init(self, cfg: Optional[dict] = None) -> None:
        """按 Nacos 的 ``neo4j:`` 段建 driver（幂等：重复调用先关旧的再建）。

        只做两件事：配置翻成构造参数（``_driver_args``）、建连接并探一次连通性
        ——起服务时报错远好于图谱构建跑到一半才报错；建约束/索引归 ``ensure_schema``。
        """
        if not HAS_NEO4J:
            raise Neo4jError(INSTALL_HINT)
        cfg = dict(cfg or {})
        uri, kwargs = self._driver_args(cfg)
        await self.close()
        driver = AsyncGraphDatabase.driver(uri, **kwargs)
        try:
            await driver.verify_connectivity()
        except Exception as e:  # noqa: BLE001  驱动异常类型很多，统一包一层可读错误
            await _drop_driver(driver)
            raise Neo4jError(f"Neo4j 连接失败（{uri}）：{str(e)[:300]}") from e
        self._driver = driver
        self._cfg = cfg
        self._database = _text(cfg, "database")
        self._loop_id = _loop_id()
        log.info(f"Neo4j 已连接: {uri} database={self._database or '默认库'}")

    async def close(self) -> None:
        if self._driver is not None:
            try:
                await _drop_driver(self._driver)
            except Exception as e:  # noqa: BLE001  退出路径上的失败不影响关停
                log.warning(f"Neo4j 驱动关闭异常（忽略）: {e}")
            self._driver = None
            self._loop_id = None

    def _driver_args(self, cfg: dict) -> tuple[str, dict[str, Any]]:
        """一份 neo4j 配置 → (uri, driver 构造参数)（init 与跨 loop 重建共用这一处）。"""
        uri = _text(cfg, "url", "uri")
        if not uri:
            raise Neo4jError("neo4j.url 未配置：Nacos 的 neo4j 段要给出 "
                             "bolt://host:7687 或 neo4j+s://host:7687")
        if "://" not in uri:
            # Nacos 上常只填 host:port：驱动会报「URI scheme '47.111.117.95' is not
            # supported」，这句既看不懂也修不了，这里补上默认 scheme 并提醒去补全
            log.warning(f"neo4j.url 缺少 scheme（{uri}），按 bolt:// 处理：建议在 Nacos 上补全")
            uri = f"bolt://{uri}"
        timeout = _num(cfg, "connection_timeout", "connection_acquisition_timeout",
                       default=DEFAULT_TIMEOUT)
        return uri, {
            "auth": (_text(cfg, "user", "username") or "neo4j", _text(cfg, "password")),
            "max_connection_pool_size": int(_num(cfg, "max_connection_pool_size",
                                                 default=DEFAULT_POOL_SIZE)),
            "connection_timeout": timeout,
            "connection_acquisition_timeout": timeout,
        }

    # ---------- 约束与索引 ----------
    async def ensure_schema(self) -> None:
        """建约束与索引（幂等；已存在时 IF NOT EXISTS 直接跳过）。"""
        for stmt in SCHEMA_STATEMENTS:
            await self.write(stmt)
        log.info(f"Neo4j 约束/索引已就绪（{len(SCHEMA_STATEMENTS)} 条）")

    # ---------- 执行 ----------
    async def run(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        """读侧统一入口：返回记录列表（dict），字段由语句里的 RETURN 决定。"""
        records, _ = await self._exec(cypher, params)
        return records

    async def write(self, cypher: str, params: Optional[dict] = None) -> dict:
        """写侧统一入口：只回四个计数（构建日志与清理回执就这几个数），不回记录。"""
        _, counters = await self._exec(cypher, params)
        return counters

    async def _exec(self, cypher: str,
                    params: Optional[dict]) -> tuple[list[dict], dict]:
        """跑一条 Cypher，一次把记录与计数都取回（读写共用这一处会话逻辑）。"""
        driver = await self._acquire()
        kwargs = {"database": self._database} if self._database else {}
        try:
            async with driver.session(**kwargs) as session:
                result = await session.run(cypher, dict(params or {}))
                records = await result.data()
                summary = await result.consume()
        except Exception as e:  # noqa: BLE001  驱动异常类型多，统一包成可读错误
            raise Neo4jError(f"Cypher 执行失败: {str(e)[:300]}") from e
        return records, _counters(summary)

    async def _acquire(self) -> Any:
        """取 driver；顺带处理「同一个单例被搬到另一个事件循环」这一类致命误用。"""
        if not HAS_NEO4J:
            raise Neo4jError(INSTALL_HINT)
        if self._driver is None:
            raise Neo4jError("Neo4j 未初始化：服务需在 bootstrap 里开 enable_neo4j，"
                             "脚本需先 await common_neo4j.init_neo4j(cfg)")
        loop_id = _loop_id()
        if self._loop_id is not None and loop_id != self._loop_id:
            # 连接池属于创建它的那个 loop，跨 loop 复用会随机报 "attached to a different loop"
            log.error(f"Neo4j 单例被跨事件循环复用（loop {self._loop_id} → {loop_id}），"
                      f"重建连接池：请检查是否在多个 loop 里共用了同一进程实例")
            await self.init(self._cfg)
        return self._driver


# ==================== 小工具 ====================
def _loop_id() -> Optional[int]:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


async def _drop_driver(driver: Any) -> None:
    """关停 driver：异步 driver 的关闭方法在 5.x 叫 close_async、6.x 只剩 close，两个都认一遍。

    只认一个会让关停路径抛 AttributeError，把真正的连接错误顶掉（init 失败时看不到原因）。
    """
    closer = getattr(driver, "close", None) or getattr(driver, "close_async", None)
    if closer is not None:
        await closer()


def _counters(summary: Any) -> dict:
    """summary → 写侧计数；没有计数信息（纯读语句）时全 0。"""
    c = getattr(summary, "counters", None)
    if c is None:
        return {"nodes_created": 0, "nodes_deleted": 0,
                "rels_created": 0, "rels_deleted": 0}
    return {"nodes_created": int(c.nodes_created), "nodes_deleted": int(c.nodes_deleted),
            "rels_created": int(c.relationships_created),
            "rels_deleted": int(c.relationships_deleted)}


# 进程内唯一实例（模块级单例：FastAPI 与 arq worker 各自 import 到自己那份）
neo4j_client = AsyncNeo4jClient()

__all__ = ["AsyncNeo4jClient", "Neo4jError", "neo4j_client", "FULLTEXT_INDEX",
           "SCHEMA_STATEMENTS"]
