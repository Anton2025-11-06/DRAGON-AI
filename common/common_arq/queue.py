# -*- coding: utf-8 -*-
"""arq 生产端封装:API 进程向队列投递任务。

设计要点:
- Redis db=0 为 arq 专属库(与业务 Redis 同实例分库隔离);
- 地址由 ARQ_REDIS_URL 常量提供,与 arq_tasks/worker_settings.py 共用同一常量
  (生产者与消费者必须指向同一个 Redis,否则任务投了没人消费);
- 队列按切片号分片:队列名 = workflow_queue:split_{split_number},生产端
  enqueue_job(split_number=...) 与消费端 WorkerSettings.SPLIT_NUMBER 取相同值才对上;
- enqueue 时指定 job_id 可实现全局幂等:同一 job_id 重复投递会被 arq 拒绝
  (execute 防重、resume 防多次触发恢复都依赖这一点)。
"""
import os
import socket
import zlib
from datetime import datetime
from typing import Optional

from arq.connections import ArqRedis, RedisSettings, create_pool

from common.common_log.log_init import log

# arq 专属 Redis 地址(db=1 为需求约定,与业务 Redis 隔离)
ARQ_REDIS_URL = "redis://10.88.128.15:26379/0"

# 切片数配置 key(值 = 切片数,存在即生效;不存在按默认 1):
#   - system 监控页修改切片数量时 SET 写该 key
#   - workflow 投递时 GET 读该 key,按 crc32(task_id) % N 轮询选切片队列
SPLIT_NUMBER_KEY = "workflow_queue:split_number"

# 切片数合法范围(监控页 InputNumber 约束 + 后端校验双保险)
MIN_SPLIT_NUMBER = 1
MAX_SPLIT_NUMBER = 32

# 队列名常量(分片队列:workflow_queue:split_{N},生产端 enqueue 与消费端 WorkerSettings 共用字符串拼法)
QUEUE_NAME = "workflow_queue"

SPLIT_NAME = "split_"

# worker 进程唯一标识(hostname:pid,分布式部署跨机唯一;同机 pid 复用残骸由启动时 DEL 兜底)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# worker 级健康 key 前缀(与 arq 内置 record_health 写入位置一致):
#   key = workflow_worker:{queue_name}:{hostname}:{pid},TTL = health_check_interval + 1 秒
# 定义在公共模块,worker_settings / tasks.workflow bootstrap 共用同一实现(单一来源)
DEFAULT_WORKER_NAME = "workflow_worker"


def worker_health_key(queue_name: str) -> str:
    """worker 进程的健康 key,格式 = workflow_worker:{queue_name}:{hostname}:{pid}。

    queue_name 形如 workflow_queue:split_2;监控端 SCAN 前缀
    f"{DEFAULT_WORKER_NAME}:{queue_name}:" 即可枚举该队列全部 worker 节点。
    """
    return f"{DEFAULT_WORKER_NAME}:{queue_name}:{WORKER_ID}"



# 全局唯一的生产端实例(懒创建,进程内复用连接池)
_arq_redis: Optional[ArqRedis] = None


async def get_arq_redis() -> ArqRedis:
    """获取 ArqRedis 生产端(首次调用时建立连接池)。"""
    global _arq_redis
    if _arq_redis is None:
        # arq 0.28 的 create_pool 直接返回 ArqRedis 实例(按 RedisSettings 建好连接池),
        # 不要再包一层 ArqRedis:否则 ArqRedis 对象被当作 connection_pool 透传给 redis-py
        # 的 Redis.__init__(后者访问 connection_kwargs 时抛 AttributeError)
        _arq_redis = await create_pool(RedisSettings.from_dsn(ARQ_REDIS_URL))
    return _arq_redis


async def enqueue_job(function: str,
                      args: Optional[list] = None,
                      *,
                      job_id: Optional[str] = None,
                      split_number: int,
                      defer_until: Optional[datetime] = None) -> bool:
    """向 arq 队列投递一个任务。

    :param function: 任务函数模块路径,必须已注册在 worker 的 WorkerSettings.functions
    :param args: 任务位置参数
    :param job_id: 全局唯一任务 ID;重复投递返回 False(幂等防重)
    :param split_number: 目标队列切片号(队列名 = workflow_queue:split_{N},
                         必须与 worker 端环境变量 SPLIT_NUMBER 取相同值)
    :param defer_until: 延迟到指定时刻再执行(默认立即)
    :return: True=投递成功, False=重复 job_id 或投递失败
    """
    arq = await get_arq_redis()
    queue_name = f"{QUEUE_NAME}:{SPLIT_NAME}{str(split_number)}"
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


async def get_split_number() -> int:
    """读取当前切片数配置(workflow_queue:split_number 的值)。

    :return: 配置的切片数;key 不存在或值非法时按默认 1(与部署缺省行为一致)
    """
    arq = await get_arq_redis()
    raw = await arq.get(SPLIT_NUMBER_KEY)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if MIN_SPLIT_NUMBER <= n <= MAX_SPLIT_NUMBER else 1


async def set_split_number(n: int) -> int:
    """写入切片数配置(供 system 监控页修改切片数量)。

    :raises ValueError: n 超出合法范围时
    :return: 写成功的切片数
    """
    if not MIN_SPLIT_NUMBER <= n <= MAX_SPLIT_NUMBER:
        raise ValueError(f"切片数必须在 {MIN_SPLIT_NUMBER}~{MAX_SPLIT_NUMBER} 之间, 实际 {n}")
    arq = await get_arq_redis()
    await arq.set(SPLIT_NUMBER_KEY, str(n))
    log.info("arq split number set: {} = {}", SPLIT_NUMBER_KEY, n)
    return n


async def next_split_number(task_id: str) -> int:
    """按任务 ID 取模轮询选择目标切片号(同一任务稳定落同一切片队列)。

    用 zlib.crc32 而非内置 hash():hash(str) 受 PYTHONHASHSEED 影响跨进程不稳定,
    同一 execution_id 在不同 API 进程会算出不同切片。crc32 确定且分布均匀。
    切片数来自 get_split_number():配置 key 存在用它,否则默认 1。
    """
    n = await get_split_number()
    return zlib.crc32(task_id.encode("utf-8")) % n + 1


async def close_arq_redis() -> None:
    """释放生产端连接池(服务关闭时调用)。"""
    global _arq_redis
    if _arq_redis is not None:
        await _arq_redis.aclose()
        _arq_redis = None
        log.info("arq producer connection pool closed")
