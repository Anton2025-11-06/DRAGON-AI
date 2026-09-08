# -*- coding: utf-8 -*-
"""arq 任务实现:工作流执行/恢复 + worker 进程生命周期钩子。

与旧 celery 方案的本质差异:
- arq worker 进程本身就是 asyncio 事件循环,任务函数直接写 async def 并 await,
  不再需要 celery 时代的线程适配层(AsyncLoopRunner)与每任务幂等 bootstrap;
- 任务函数只负责「从 DB 重建运行时并驱动引擎」,执行记录/状态/快照全程以 DB 为权威:
  engine 在每个节点执行前调用状态检查钩子,读到 CANCELLED/PAUSED 就按 DB 状态收尾;
- bootstrap 在 worker 进程启动时执行一次(等价 FastAPI 的 lifespan):
  加载 Nacos 配置 → 初始化 MySQL/Redis/httpx 连接池。事件不再需要后台桥:
  engine 节点执行时经 EventBus 的 pub hook 实时 PUBLISH 到 Redis 频道(含
  node.delta),API 进程的 SSE 订阅者 SUBSCRIBE 实时消费(Pub/Sub 方案)。
"""
import asyncio
import logging
import os
import sys
from urllib.parse import quote_plus

from common.common_arq.queue import worker_health_key
from common.common_constants.constant import SERVICE_WORKFLOW
from common.common_httpx.httpx import httpx_pool
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_nacos.config import Config
from common.common_nacos.nacos_client import NacosClient
from common.common_redis import redis
from service.service_workflow.services.workflow_execution_service import WorkflowExecutionService

# bootstrap 幂等标记(同一进程内只初始化一次,防止异常重启路径重复初始化)
_BOOTSTRAPPED = False


async def bootstrap(ctx: dict) -> None:
    """arq worker 进程启动钩子(每 worker 进程执行一次)。

    arq 0.28 钩子/任务函数的第一个参数都是 ctx 字典(非 WorkerContext 类型,
    该符号在此版本不存在),内装 redis 连接池等;这里只用于幂等标记。
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    # 0. 清理同 hostname:pid 的旧健康 key 残骸(pid 复用场景:上次未优雅退出的
    # 节点 key 可能仍未过期,启动即 DEL 兜底,避免监控端误判为存活节点)
    try:
        await ctx["redis"].delete(worker_health_key(os.environ.get("ARQ_QUEUE", "workflow")))
    except Exception as e:  # noqa: BLE001
        log.warning("clean stale worker health key failed: {}", e)

    # Windows 下使用 Selector 事件循环(与 uvicorn 多 worker 相同的策略,
    # 保证 Redis/MySQL 异步驱动行为稳定)
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 1. 加载 workflow 服务的 Nacos 配置(内含 mysql/redis 连接参数)
    nacos_service = NacosClient(
        user_name=os.environ.get("nacos_name", Config.nacos_name),
        password=os.environ.get("nacos_password", Config.nacos_password),
        server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
        service_name=SERVICE_WORKFLOW,
        ip=None,
        port=0,
        namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
        log_level=logging.NOTSET,
    )
    await nacos_service.init()
    yml_config = await nacos_service.get_config_content(SERVICE_WORKFLOW)

    # 2. 初始化 MySQL / Redis / httpx 连接池(与 FastAPI 服务共用同一套配置)
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
    redis_cfg = yml_config.get("redis", {})
    await redis.client.init(
        host=redis_cfg.get("host"),
        port=int(redis_cfg.get("port")),
        password=redis_cfg.get("password", ""),
        max_connections=redis_cfg.get("max_connections") or 200,
        db=int(redis_cfg.get("db") or 0),
    )
    httpx_pool.init()

    log.info("arq worker bootstrap done, queue={}", ctx.get("queue_name", "workflow"))
    _BOOTSTRAPPED = True


async def shutdown(ctx: dict) -> None:
    """arq worker 进程退出钩子:释放连接池。

    ctx 为 arq 传入的上下文字典(见 bootstrap 注释)。
    """
    await mysql_client.close()
    await redis.client.close()
    log.info("arq worker shutdown done")


async def execute_workflow(ctx: dict, execution_id: str) -> None:
    """执行工作流(execute-async 接口投递的任务入口)。

    执行记录已在 API 进程创建(状态 RUNNING),这里从 DB 重建运行时并驱动引擎;
    引擎各节点执行前会检查 DB 状态,取消/暂停由 DB 状态控制(需求 5)。
    """
    await WorkflowExecutionService.run_in_worker(execution_id)


async def resume_workflow(ctx: dict, execution_id: str) -> None:
    """恢复暂停的工作流(resume 接口投递的任务入口)。

    恢复语义(需求 5):resume 接口已把 DB 状态改回 RUNNING、并合并 edit 数据写入
    variables 快照;这里从 DB 重建运行时 → restore 快照 → run() 从 pending 节点继续。
    """
    await WorkflowExecutionService.resume_in_worker(execution_id)