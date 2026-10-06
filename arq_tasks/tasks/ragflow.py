# -*- coding: utf-8 -*-
"""arq rag 流水线：文档解析 → 分块 → 向量化 + worker 生命周期钩子。

知识图谱构建已拆到独立的 graphflow 流水线（tasks/graphflow.py）：图谱逐批走大模型抽取，
比文档解析还耗时，和解析挤在同一队列会把 worker 并发槽位占满，故分队列、分 worker 独立扩缩。
本模块的 bootstrap 已抽成 _bootstrap_common，graphflow 直接复用，两条流水线的后端依赖完全一致。

与 workflow 流水线（tasks/workflow.py）的关系与差异：
- 同一套 arq 机制、同一个 Redis 实例，只靠队列名隔离（rag_queue:* 与 workflow_queue:*），
  两边互不偷任务：知识库解析排队再久也不该把工作流执行卡住，反之亦然（SPEC §4.1）；
- 本流水线**全 CPU**：解析引擎里 docling 是 pip 依赖、minerU 走私有化 HTTP、embedding 与
  视频理解都走 HTTP API，worker 进程内不承载任何模型算力，故不需要 GPU 相关参数（SPEC §4.2）；
- 任务函数只负责「按 doc_id 驱动一遍流水线」，权威状态在 MySQL（tb_document.status 等）、
  进度在 Redis；API 进程只入队与读进度，不重复实现任何处理逻辑。
  真正的解析/分块/写向量/抽实体全在 service.service_rag.services 里，与 API 侧共用同一套代码。
"""
import asyncio
import logging
import os
import sys
from typing import Optional
from urllib.parse import quote_plus

from common.common_arq.queue import (
    PIPELINE_RAG,
    CustomRedisSettings,
    worker_health_key,
)
from common.common_constants.constant import ARQ_RAGFLOW, SERVICE_RAG
from common.common_constants.rag_constant import (
    RAG_TASK_PARSE,
    RAG_TASK_KB_PURGE,
    RAG_TASK_MEDIA,
    rag_task_name,
)
from common.common_es import close_es, init_es
from common.common_httpx.httpx import httpx_pool
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_nacos.config import Config
from common.common_nacos.nacos_client import nacos_client
from common.common_neo4j import close_neo4j, init_neo4j
from common.common_redis import redis
from common.common_storage import close_storage, init_storage

# 任务函数名（worker 注册与 API 侧 enqueue 共用 rag_constant 这一处来源，不一致就是投了没人消费）
# 图谱构建（build_document_graph）已移到 graphflow 流水线，rag worker 不再注册/消费它，
# 否则慢图谱任务会把解析 worker 的并发槽位占满（见 tasks/graphflow.py 顶部说明）
TASK_FUNCTIONS = [rag_task_name(name) for name in (
    RAG_TASK_PARSE, RAG_TASK_MEDIA, RAG_TASK_KB_PURGE)]


async def prepare_config():
    """worker_settings_rag 模块 import 期读一次 arq 的 Redis 配置。

    写到 CustomRedisSettings.yml_rag（不是 yml）：yml 那一份属于 workflow 流水线，
    两条流水线的 arq Redis 可以不是同一个实例，共用一个字段会互相覆盖。

    读完即 close：单例被打回未初始化，bootstrap 里再 init 一次拿服务配置（幂等守卫
    会跳过已初始化的情况，不先关就再也读不到配置）。
    """
    await nacos_client.init(
        user_name=os.environ.get("nacos_name", Config.nacos_name),
        password=os.environ.get("nacos_password", Config.nacos_password),
        server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
        service_name=SERVICE_RAG,
        namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
        log_level=logging.NOTSET,
    )
    CustomRedisSettings.yml_rag = await nacos_client.get_config_content(ARQ_RAGFLOW)
    await nacos_client.close()


# bootstrap 幂等标记（同一进程内只初始化一次，防止异常重启路径重复初始化）
_BOOTSTRAPPED = False


async def _ainit_business_redis(redis_cfg: dict, arq_setting) -> None:
    """初始化业务 Redis（文档进度/取消/流水线锁键用），与 arq 队列连接池分开。

    配置里给了 host/port 就按 host/port 连；只给了 arq 的 dsn（url）就从 dsn 反推——
    两条流水线现在共用同一实例，逼运维再填写一遍 host/port 只会多出一次对不上的机会。
    """
    host = redis_cfg.get("host") or arq_setting.host
    port = int(redis_cfg.get("port") or arq_setting.port)
    await redis.client.init(
        host=host,
        port=port,
        password=redis_cfg.get("password", arq_setting.password) or "",
        max_connections=redis_cfg.get("max_connections") or 200,
        # db 留空时跟随 arq dsn 的库号：进度键写在 API 进程与 worker 都能看到的同一个库
        db=int(redis_cfg.get("db") if redis_cfg.get("db") is not None else arq_setting.database),
    )


async def _bootstrap_common(ctx: dict, *, queue_name: str, pipeline: str,
                           functions: list[str], worker_tag: str) -> None:
    """worker 进程启动钩子的公共实现（rag / graph 两条流水线共用一份，只差队列名与流水线标识）。

    两条流水线的后端依赖完全一致（Nacos→MySQL/Redis/httpx/storage/ES/Neo4j→rag_settings）：
    图谱构建需要 Neo4j + 业务 Redis + httpx + rag_settings，文档解析还需要 ES + storage，
    干脆都初始化——graphflow 复用本函数后 run_build 行为与拆分前完全一致，不会因少装
    某个后端在第一个任务上炸「client not initialized」。

    arq 0.28 钩子/任务函数的第一个参数都是 ctx 字典（内装 redis 连接池等），
    这里只用于清理残留健康 key 与幂等标记。
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    # 0. 清理本 worker（hostname:pid）残留的旧健康 key（pid 复用场景：上次未优雅退出的
    # 节点 key 可能仍未过期，启动即 DEL 兜底，避免监控端误判为存活节点）
    try:
        await ctx["redis"].delete(worker_health_key(queue_name, pipeline))
    except Exception as e:  # noqa: BLE001
        log.warning("clean stale {} worker health key failed: {}", worker_tag, e)

    # Windows 下使用 Selector 事件循环（与 uvicorn 多 worker 相同的策略，
    # 保证 Redis/MySQL/ES 异步驱动行为稳定）
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 1. 加载 worker 的 Nacos 配置（内含 mysql/storage/es/neo4j/mineru 各段）。
    # data_id 用 arq_ragflow：rag 与 graph 两条流水线共用这一段，后端与并发只配一份好调。
    await nacos_client.init(
        user_name=os.environ.get("nacos_name", Config.nacos_name),
        password=os.environ.get("nacos_password", Config.nacos_password),
        server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
        service_name=SERVICE_RAG,
        namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
        log_level=logging.NOTSET,
    )
    yml_config = await nacos_client.get_config_content(ARQ_RAGFLOW) or {}

    # 2. MySQL / Redis / httpx 连接池（与 service_rag 进程同一套配置）
    mysql_cfg = yml_config.get("mysql", {})
    await mysql_client.init(
        host=mysql_cfg.get("host"),
        port=int(mysql_cfg.get("port")),
        user=mysql_cfg.get("user"),
        password=quote_plus(mysql_cfg.get("password")),
        database=mysql_cfg.get("database"),
        pool_size=int(mysql_cfg.get("pool_size")),
        max_overflow=int(mysql_cfg.get("max_overflow")),
        echo=mysql_cfg.get("echo"),
    )
    arq_setting = CustomRedisSettings.get_redis_setting(pipeline)
    await _ainit_business_redis(yml_config.get("redis", {}), arq_setting)
    httpx_pool.init()

    # 3. 统一存储后端：原件与解析产物 sidecar 由 API 进程写、worker 进程读，
    # 两处的 storage 段必须同源，未初始化会退到本地后端 → worker 找不到文件。
    await init_storage(yml_config.get("storage", {}))

    # 4. ES / Neo4j（全局异步单例，见 common_es / common_neo4j）
    await init_es(yml_config.get("es"))
    await init_neo4j(yml_config.get("neo4j"))

    # 5. rag 运行参数装载（批量/并发/提示词/minerU 地址）：解析与向量化的所有量级参数都从
    # 这一份取，漏装会静默走代码默认值——大文档批量打满上游 HTTP 时最难定位。
    # 传 arq_ragflow 这一份 yml：worker 只读它，不再回 service_rag 段取第二份口径。
    from service.service_rag.services import rag_settings

    await rag_settings.bootstrap(yml_config)

    log.info("arq {} worker bootstrap done, queue={}, functions={}",
             worker_tag, queue_name, len(functions))
    _BOOTSTRAPPED = True


async def bootstrap(ctx: dict) -> None:
    """rag worker 进程启动钩子（每 worker 进程执行一次）：队列/流水线取 ragflow。"""
    from arq_tasks.worker_settings_rag import WorkerSettingsRag

    await _bootstrap_common(
        ctx,
        queue_name=WorkerSettingsRag.queue_name,
        pipeline=PIPELINE_RAG,
        functions=TASK_FUNCTIONS,
        worker_tag="rag",
    )


async def shutdown(ctx: dict) -> None:
    """rag worker 进程退出钩子：释放连接池（与 bootstrap 逆序，先关外围后端）。"""
    try:
        await close_neo4j()
    except Exception as e:  # noqa: BLE001
        log.warning("neo4j close failed: {}", e)
    try:
        await close_es()
    except Exception as e:  # noqa: BLE001
        log.warning("elasticsearch close failed: {}", e)
    await mysql_client.close()
    await redis.client.close()
    # worker 不注册服务，nacos 只用于启动时拉配置；这里的 close 对未初始化单例是空操作
    await nacos_client.close()
    try:
        await close_storage()
    except Exception as e:  # noqa: BLE001
        log.warning("storage backend close failed: {}", e)
    log.info("arq rag worker shutdown done")


async def parse_document(ctx: dict, doc_id: int, options: Optional[dict] = None) -> None:
    """doc 型知识库的解析：取原件 → 解析 → 预处理 → 分块 → 向量化 → 写 ES → 回写状态。

    任务体只做「驱动 + 兜异常」：状态机流转、进度写 Redis、失败落 error_msg 都在
    parse_service 里（API 侧的手工重跑走同一个方法，两条路径不可能跑出两种结果）。
    ``options`` 是投递时带上的任务选项（目前只有 sidecar 复用策略）。
    取消在阶段边界生效（rag_constant.RAG_CANCEL_PREFIX），被取消的文档回到 PENDING 等重试。
    """
    from service.service_rag.services.parse_service import RagParseService

    await RagParseService.run_parse(doc_id, options)


async def parse_media_document(ctx: dict, doc_id: int,
                                options: Optional[dict] = None) -> None:
    """image / audio_video 型知识库的解析：多模态向量 +（音视频）视频理解摘要。

    图片型不生成描述、不做图谱（SPEC §8）；音视频型不抽帧不 ASR，只出一条全局向量（SPEC §9），
    所以这条流水线比 doc 型短得多，单独一个任务函数——混进 parse_document 就要在
    流水线里堆一层「这是哪种库」的分支，两侧配置项还得各自解释一遍。
    """
    from service.service_rag.services.parse_service import RagParseService

    await RagParseService.run_media_parse(doc_id, options)


async def purge_knowledge_base(ctx: dict, kb_id: int, doc_ids: Optional[list[int]] = None) -> None:
    """删库/删文档后的底层清理：ES 按 kb_id（或指定 doc_id）delete_by_query，Neo4j 清该库子图。

    放异步是因为一个库里可能几万条切片，同步 delete_by_query 会把 HTTP 请求挂到超时；
    MySQL 侧的业务行在 API 进程里已经软删，这里只负责把裸查存储擦干净。
    """
    from service.service_rag.services.parse_service import RagParseService

    await RagParseService.run_purge(kb_id, doc_ids)
