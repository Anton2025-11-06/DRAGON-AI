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

from common.common_log.log_init import log
from common.common_middleware.exception_handler import register_exception_handlers
from common.common_middleware.request_log_middleware import RequestLogMiddleware
from common.common_middleware.token_check_middleware import TokenCheckMiddleware
from common.common_middleware.rate_limit_middleware import RateLimitMiddleware
from common.common_middleware.operate_log_middleware import OperateLogMiddleware
from common.common_mysql.mysql import mysql_client
from common.common_nacos.config import Config
from common.common_nacos.nacos_client import NacosClient
from common.common_redis import redis
from common.common_httpx.httpx import httpx_pool
from common.common_storage import get_storage, init_storage


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


async def _init_storage(storage_cfg: dict):
    # 统一存储后端（本地 / MinIO 等 S3）：未配置时默认本地，无外部依赖；
    # 配置了 s3 但连接失败会抛错并阻断启动（避免静默降级造成数据写错位置）。
    await init_storage(storage_cfg)


def create_app(service_name: str,
               default_port: int,
               routers: list[APIRouter],
               enable_redis: bool = True,
               enable_mysql: bool = True,
               enable_token_check: bool = False,
               enable_rate_limit: bool = False,
               enable_operate_log: bool = False,
               enable_httpx_pool: bool = True
               ) -> FastAPI:
    """
    统一的 FastAPI 服务引导工厂：
    1. lifespan 中完成 Nacos 注册/配置拉取、Redis/MySQL 连接池初始化与释放
    2. 统一挂载 CORS、请求日志、Token鉴权、限流中间件和全局异常处理
    3. 注册业务路由
    4. 启动后执行扩展初始化钩子（如 Milvus 连接）

    :param service_name: 服务名（同时作为 Nacos data_id 和注册名）
    :param default_port: 默认端口（可被环境变量 {service_name}_port 覆盖）
    :param routers: 业务路由列表
    :param enable_redis: 是否初始化 Redis 连接池
    :param enable_mysql: 是否初始化 MySQL 连接池
    :param enable_token_check: 是否开启 Token 鉴权中间件
    :param enable_rate_limit: 是否开启 Redis 限流中间件（网关使用）
    :param enable_operate_log: 是否开启操作日志中间件（RBAC 审计使用，需 MySQL）
    """
    port = int(os.environ.get(service_name + "_port", default_port))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nacos_service = NacosClient(
            user_name=os.environ.get("nacos_name", Config.nacos_name),
            password=os.environ.get("nacos_password", Config.nacos_password),
            server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
            service_name=service_name,
            # TODO 自动获取 实例IP
            ip=os.environ.get("service_ip"),
            port=port,
            namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
            log_level=Config.nacos_log_level,
        )

        await nacos_service.init()
        # 供网关等需要在运行时做服务发现的场景使用
        app.state.nacos_service = nacos_service
        await nacos_service.register_service()
        yml_config = await nacos_service.get_config_content(service_name)

        # 暴露给业务扩展（如 Milvus、向量化配置），保持 app.state.config 全服务可用
        app.state.config = yml_config

        try:
            if enable_redis:
                await _init_redis(yml_config.get("redis", {}))
            if enable_mysql:
                await _init_mysql(yml_config.get("mysql", {}))
            if enable_httpx_pool:
                _init_httpx_pool()
            await _init_storage(yml_config.get("storage", {}))
        except Exception as e:
            # 初始化失败时回滚 Nacos 注册，避免注册了不可用实例
            log.error(f"Service {service_name} init dependencies failed: {str(e)}")
            try:
                await nacos_service.deregister_service()
                await nacos_service.close_config_client()
            except Exception as deregister_err:
                log.warning(f"Nacos deregister skipped: {deregister_err}")
            raise e

        # 网关启动时自动加载 Redis 中的限流策略（无配置则默认无限流）
        if enable_rate_limit:
            await RateLimitMiddleware.reload_from_redis()

        yield

        # Nacos 不可用时客户端可能未初始化/连接失效，注销失败不阻断进程退出
        try:
            await nacos_service.deregister_service()
            await nacos_service.close_config_client()
        except Exception as e:
            log.warning(f"Nacos deregister skipped: {e}")
        if enable_redis:
            await redis.client.close()
        if enable_mysql:
            await mysql_client.close()
        try:
            await get_storage().close()
        except Exception as e:
            log.warning(f"storage backend close skipped: {e}")

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
