# -*- coding: utf-8 -*-
"""arq 生产端封装:API 进程向队列投递任务。

设计要点:
- Redis db=1 为需求约定(与业务 Redis 隔离,避免 key 互相干扰);
- DSN 优先读环境变量 ARQ_REDIS_URL,与 arq_tasks/worker_settings.py 共用同一常量
  (生产者与消费者必须指向同一个 Redis,否则任务投了没人消费);
- enqueue 时指定 job_id 可实现全局幂等:同一 job_id 重复投递会被 arq 拒绝
  (execute 防重、resume 防多次触发恢复都依赖这一点)。
"""
import os
import socket
from datetime import datetime
from typing import Optional

from arq.connections import ArqRedis, RedisSettings, create_pool

from common.common_log.log_init import log

# arq 专属 Redis 地址(db=1 为需求约定,与业务 Redis 隔离)
ARQ_REDIS_DSN = os.environ.get("ARQ_REDIS_URL", "redis://10.88.128.15:26379/0")

# worker 进程唯一标识(hostname:pid,分布式部署跨机唯一;同机 pid 复用残骸由启动时 DEL 兜底)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


# 节点级健康 key 前缀(与 arq 内置 record_health 写入位置一致):
#   key = {queue_name}:workers:{hostname}:{pid},TTL = health_check_interval + 1 秒
# 每个 worker 进程一个 key → SCAN 前缀即可枚举全部存活/失联节点(替代 arq 默认的
# 全 worker 共享 key,后者后写覆盖无法区分节点)。
def worker_health_key(queue_name: str) -> str:
    return f"{queue_name}:workers:{WORKER_ID}"

# 默认队列名(与 worker 端环境变量 ARQ_QUEUE 保持一致)
DEFAULT_QUEUE_NAME = "workflow"

# 全局唯一的生产端实例(懒创建,进程内复用连接池)
_arq_redis: Optional[ArqRedis] = None


async def get_arq_redis() -> ArqRedis:
    """获取 ArqRedis 生产端(首次调用时建立连接池)。"""
    global _arq_redis
    if _arq_redis is None:
        # arq 0.28 的 create_pool 直接返回 ArqRedis 实例(按 RedisSettings 建好连接池),
        # 不要再包一层 ArqRedis:否则 ArqRedis 对象被当作 connection_pool 透传给 redis-py
        # 的 Redis.__init__(后者访问 connection_kwargs 时抛 AttributeError)
        _arq_redis = await create_pool(RedisSettings.from_dsn(ARQ_REDIS_DSN))
    return _arq_redis


async def enqueue_job(function: str,
                      args: Optional[list] = None,
                      *,
                      job_id: Optional[str] = None,
                      queue_name: str = DEFAULT_QUEUE_NAME,
                      defer_until: Optional[datetime] = None) -> bool:
    """向 arq 队列投递一个任务。

    :param function: 任务函数模块路径,必须已注册在 worker 的 WorkerSettings.functions
    :param args: 任务位置参数
    :param job_id: 全局唯一任务 ID;重复投递返回 False(幂等防重)
    :param queue_name: 目标队列名(不同业务队列互不影响)
    :param defer_until: 延迟到指定时刻再执行(默认立即)
    :return: True=投递成功, False=重复 job_id 或投递失败
    """
    arq = await get_arq_redis()
    job = await arq.enqueue_job(
        function,
        *(args or []),
        _job_id=job_id,
        _queue_name=queue_name,
        _defer_until=defer_until,
    )
    if job is None:
        # arq 对已存在的 job_id 返回 None(任务全局去重)
        log.warning("arq enqueue skipped(duplicated job_id): {} job_id={}", function, job_id)
        return False
    log.info("arq enqueue ok: {} job_id={} queue={}", function, job_id or job.job_id, queue_name)
    return True


async def close_arq_redis() -> None:
    """释放生产端连接池(服务关闭时调用)。"""
    global _arq_redis
    if _arq_redis is not None:
        await _arq_redis.aclose()
        _arq_redis = None
        log.info("arq producer connection pool closed")