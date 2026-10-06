import os
import socket
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus

import yaml

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request

from common.common_arq.queue import PIPELINE_RAG, CustomRedisSettings, get_arq_redis
from common.common_constants.constant import ARQ_RAGFLOW, ARQ_WORKFLOW
from common.common_log.log_init import log
from common.common_middleware.exception_handler import register_exception_handlers
from common.common_middleware.request_log_middleware import RequestLogMiddleware
from common.common_middleware.token_check_middleware import TokenCheckMiddleware
from common.common_middleware.rate_limit_middleware import RateLimitMiddleware
from common.common_middleware.operate_log_middleware import OperateLogMiddleware
from common.common_mysql.mysql import mysql_client
from common.common_nacos.config import Config
from common.common_nacos.nacos_client import nacos_client
from common.common_redis import redis
from common.common_httpx.httpx import httpx_pool
from common.common_storage import close_storage, init_storage


async def _init_redis(redis_cfg: dict):
    # common_redis.redis 模块导出单例 client（AsyncRedisClient），统一经其初始化
    # Nacos 配置允许缺省 db/max_connections，此处兜底默认值
    await redis.client.init(
        host=redis_cfg.get("host"),
        port=int(redis_cfg.get("port")),
        password=redis_cfg.get("password", ""),
        max_connections=redis_cfg.get("max_connections") or 200,
        db=int(redis_cfg.get("db") or 0)
    )


async def _init_mysql(mysql_cfg: dict):
    await mysql_client.init(
        host=mysql_cfg.get("host"),
        port=int(mysql_cfg.get("port")),
        user=mysql_cfg.get("user"),
        password=quote_plus(mysql_cfg.get("password")),
        database=mysql_cfg.get("database"),
        pool_size=int(mysql_cfg.get("pool_size")),
        max_overflow=int(mysql_cfg.get("max_overflow")),
        echo=mysql_cfg.get("echo")
    )


def _init_httpx_pool():
    httpx_pool.init()


def get_container_default_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 53))
            return sock.getsockname()[0]
    except:
        return "127.0.0.1"


# ES / Neo4j 延迟导入：只有开了开关的服务（目前只有 service_rag）才需要这两个客户端包，
# 放到模块顶层会让 login/gateway 之类不碰知识库的服务也被迫装齐依赖（装不上就是起不来）
async def _init_es(es_cfg: dict):
    from common.common_es import init_es

    await init_es(es_cfg)


async def _init_neo4j(neo4j_cfg: dict):
    from common.common_neo4j import init_neo4j

    await init_neo4j(neo4j_cfg)


async def _close_vector_backends(enable_es: bool, enable_neo4j: bool):
    if enable_neo4j:
        from common.common_neo4j import close_neo4j

        await close_neo4j()
    if enable_es:
        from common.common_es import close_es

        await close_es()


def create_app(service_name: str,
               default_port: int,
               routers: list[APIRouter],
               enable_redis: bool = True,
               enable_mysql: bool = True,
               enable_token_check: bool = False,
               enable_rate_limit: bool = False,
               enable_operate_log: bool = False,
               enable_httpx_pool: bool = True,
               enable_storage: bool = False,
               enbale_arq_workflow_redis: bool = False,
               enable_arq_ragflow_redis: bool = False,
               enable_es: bool = False,
               enable_neo4j: bool = False,
               on_ready: Optional[Callable[[dict], Awaitable[None]]] = None
               ) -> FastAPI:
    """
    统一的 FastAPI 服务引导工厂：
    1. lifespan 中完成 Nacos 注册/配置拉取、Redis/MySQL/存储/ES/Neo4j 连接初始化与释放
    2. 统一挂载 CORS、请求日志、Token鉴权、限流中间件和全局异常处理
    3. 注册业务路由
    4. 需要哪个后端由开关决定，不在各服务里自己写一遍 lifespan：连接池的用法全项目只有一份

    :param service_name: 服务名（同时作为 Nacos data_id 和注册名）
    :param default_port: 默认端口（可被环境变量 {service_name}_port 覆盖）
    :param routers: 业务路由列表
    :param enable_redis: 是否初始化 Redis 连接池
    :param enable_mysql: 是否初始化 MySQL 连接池
    :param enable_token_check: 是否开启 Token 鉴权中间件
    :param enable_rate_limit: 是否开启 Redis 限流中间件（网关使用）
    :param enable_operate_log: 是否开启操作日志中间件（RBAC 审计使用，需 MySQL）
    :param enbale_arq_workflow_redis: 是否建工作流投递队列的连接池（读 Nacos 的 arq_workflow 段）
    :param enable_arq_rag_redis: 是否建知识库解析队列的连接池（读 arq_ragflow 段）。
        生产者与 rag worker 必须读到同一段的 redis.url，否则任务投了没人消费
    :param enable_es: 是否初始化 Elasticsearch 全局单例（读 Nacos 的 es 段）
    :param enable_neo4j: 是否初始化 Neo4j 全局单例（读 Nacos 的 neo4j 段）
    :param on_ready: 全部依赖初始化成功后的回调，收到本服务的 Nacos 配置字典。
        给「配置要落进全局单例」的模块用（如 service_rag 的 rag_settings.bootstrap）：
        放在每个请求里懒加载会让首个请求读到默认值，放在 import 期又拿不到 Nacos 配置
    """
    port = int(os.environ.get(service_name + "_port", default_port))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 与 mysql_client 同一用法：直接用模块级单例，不再自行构造/挂 app.state
        await nacos_client.init(
            user_name=os.environ.get("nacos_name", Config.nacos_name),
            password=os.environ.get("nacos_password", Config.nacos_password),
            server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
            service_name=service_name,
            # TODO 自动获取 实例IP
            ip=get_container_default_ip(),
            port=port,
            namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
            log_level=Config.nacos_log_level,
        )
        # await nacos_client.register_service()
        yml_config = await nacos_client.get_config_content(service_name)

        # 暴露给业务扩展（如检索参数、向量化配置），保持 app.state.config 全服务可用
        app.state.config = yml_config

        try:
            if enable_redis:
                await _init_redis(yml_config.get("redis", {}))
                log.info("Redis initialized")
            if enable_mysql:
                await _init_mysql(yml_config.get("mysql", {}))
                log.info("MySQL initialized")
            if enable_httpx_pool:
                _init_httpx_pool()
                log.info("HTTPX pool initialized")
            if enable_storage:
                # 统一存储入口（common_storage.upload/download/delete/exists）；
                # 未配置 storage 段时默认本地后端，配了 OSS 但凭证错误这里直接阻断启动
                log.info("Storage initialized")
                await init_storage(yml_config.get("storage", {}))
            if enbale_arq_workflow_redis:
                CustomRedisSettings.yml = await nacos_client.get_config_content(ARQ_WORKFLOW)
                await get_arq_redis()
                log.info("Arq workflow Redis initialized")
            if enable_arq_ragflow_redis:
                # 与 rag worker 同一段配置（arq_ragflow）：两个流水线可以不是同一个 Redis 实例
                CustomRedisSettings.yml_rag = await nacos_client.get_config_content(ARQ_RAGFLOW)
                await get_arq_redis(PIPELINE_RAG)
                log.info("Arq rag Redis initialized")
            if enable_es:
                # 单索引 rag_knowledge_chunk 承载全部知识库；建索引与探测分词器在 init_es 里做
                await _init_es(yml_config.get("es", {}))
                log.info("Elasticsearch initialized")
            if enable_neo4j:
                await _init_neo4j(yml_config.get("neo4j", {}))
                log.info("Neo4j initialized")
            if on_ready is not None:
                # 仍在 try 内：业务侧配置装配失败等于服务不可用，按下面的口径注销并阻断启动
                await on_ready(yml_config)

        except Exception as e:
            # 初始化失败时回滚 Nacos 注册，避免注册了不可用实例
            log.error(f"Service {service_name} init dependencies failed: {str(e)}")
            try:
                await nacos_client.deregister_service()
                await nacos_client.close()
            except Exception as deregister_err:
                log.warning(f"Nacos deregister skipped: {deregister_err}")
            raise e

        # 网关启动时自动加载 Redis 中的限流策略（无配置则默认无限流）
        if enable_rate_limit:
            await RateLimitMiddleware.reload_from_redis()

        yield

        # Nacos 不可用时客户端可能未初始化/连接失效，注销失败不阻断进程退出
        try:
            await nacos_client.deregister_service()
            await nacos_client.close()
            if enable_redis:
                await redis.client.close()
            if enable_mysql:
                await mysql_client.close()
            if enable_storage:
                # 异步存储后端（阿里云 OSS 的 aiohttp 会话）必须显式关闭，否则退出时刷
                # "Unclosed client session"；本地后端的 close() 是空实现
                await close_storage()
            if enable_es or enable_neo4j:
                # 两个向量/图后端都是异步连接池，不关会在进程退出时报未关闭告警
                await _close_vector_backends(enable_es, enable_neo4j)
            if enbale_arq_workflow_redis or enable_arq_ragflow_redis:
                # 按已建的池全部释放（未建过的流水线是空操作）
                from common.common_arq.queue import close_arq_redis

                await close_arq_redis()
        except Exception as e:
            log.warning(f"Some deregister failed: {e}")

    app = FastAPI(title=service_name, lifespan=lifespan)

    app.add_middleware(RequestLogMiddleware)

    if enable_rate_limit:
        app.add_middleware(RateLimitMiddleware)

    if enable_token_check:
        app.add_middleware(TokenCheckMiddleware)

    if enable_operate_log:
        app.add_middleware(OperateLogMiddleware)

    # CORS 必须位于中间件栈最外层（最后添加）：跨域 preflight（OPTIONS）请求由 CORSMiddleware
    # 直接短路返回（200 + Access-Control-Allow-* 头），避免被 Token 鉴权/限流等外层中间件拦截
    # 返回 403 且缺失 CORS 头，导致浏览器端 CORS 报错（同源代理访问时无 preflight，注意此差异）
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_headers=["*"],
        allow_methods=["*"],
    )

    register_exception_handlers(app)

    for router in routers:
        app.include_router(router)

    return app
