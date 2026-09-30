# -*- coding: utf-8 -*-
"""arq 生产端封装：API 进程向队列投递任务（workflow / rag 双流水线）。

设计要点：
- 两条流水线**只靠队列名隔离**，共用同一 Redis 实例：workflow_queue:split_{N} 与
  rag_queue:split_{N} 各自入队、各自消费，一套卡住不会拖死另一套（SPEC §4）；
- Redis 地址与 worker 同源：arq 消费端的 arq Redis 从 Nacos 的 arq_workflow /
  arq_ragflow 段的 redis.url 读，生产端必须读到**同一段**同一个 key，否则任务投了没人消费
  （两条流水线的 redis 段可以不同实例，故连接池按流水线索引，不做单一全局池）；
- 队列按切片号分片：队列名 = {基础队列名}:split_{split_number}，生产端
  enqueue_job(split_number=...) 与消费端 WorkerSettings.SPLIT_NUMBER 取相同值才对上；
  切片数配置也是每条流水线一份（知识库摄取放量时只加 rag 的切片，不动工作流队列）；
- enqueue 时指定 job_id 可实现全局幂等：同一 job_id 重复投递会被 arq 拒绝
  （execute 防重、resume 防多次触发恢复、同一文档重复入队都依赖这一点）。
"""
import os
import socket
import zlib
from datetime import datetime
from typing import Optional

from arq.connections import ArqRedis, RedisSettings, create_pool

from common.common_log.log_init import log

# ==================== 双流水线标识 ====================
# 必须先于 CustomRedisSettings 定义：类方法签名里的默认值在 import 期就要求值
PIPELINE_WORKFLOW = "workflow"
PIPELINE_RAG = "rag"

# 队列名常量（分片队列：{基础名}:split_{N}，生产端 enqueue 与消费端 WorkerSettings 共用同一拼法）
QUEUE_NAME = "workflow_queue"
RAG_QUEUE_NAME = "rag_queue"

SPLIT_NAME = "split_"

# 切片数合法范围（监控页 InputNumber 约束 + 后端校验双保险）
MIN_SPLIT_NUMBER = 1
MAX_SPLIT_NUMBER = 100

# worker 进程唯一标识（hostname:pid，分布式部署跨机唯一；同机 pid 复用残骸由启动时 DEL 兜底）
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# worker 级健康 key 的 worker 名（与 arq 内置 record_health 写入位置一致）：
#   key = {worker_name}:{queue_name}:{hostname}:{pid}，TTL = health_check_interval + 1 秒
# 定义在公共模块，worker_settings / tasks 的 bootstrap / system 监控端共用同一实现（单一来源）
DEFAULT_WORKER_NAME = "workflow_worker"
RAG_WORKER_NAME = "rag_worker"

# 每条流水线的（基础队列名, 切片数配置 key, worker 健康 key 前缀）。
# 切片数 key 存到 Redis 而不是 Nacos：监控页改完即时生效，不需要重启 worker。
_PIPELINE_CFG: dict[str, tuple[str, str, str]] = {
    PIPELINE_WORKFLOW: (QUEUE_NAME, "workflow_queue:split_number", DEFAULT_WORKER_NAME),
    PIPELINE_RAG: (RAG_QUEUE_NAME, "rag_queue:split_number", RAG_WORKER_NAME),
}

# workflow 流水线的切片数配置 key（值 = 切片数，存在即生效；不存在按默认 1）：
#   - system 监控页修改切片数量时 SET 写该 key
#   - 投递时 GET 读该 key，按 crc32(task_id) % N 轮询选切片队列
# 保留这个名字是因为监控页与既有代码都按常量引用它
SPLIT_NUMBER_KEY = _PIPELINE_CFG[PIPELINE_WORKFLOW][1]


class CustomRedisSettings:
    """两条流水线的 arq Redis 配置载体（由各任务的 prepare_config 写入）。

    不在 import 期读 Nacos：worker_settings 的模块级代码只能同步拿到已解析好的字典。
    """
    yml: dict = {}          # arq_workflow 段（workflow 流水线）
    yml_rag: dict = {}      # arq_ragflow 段（rag 流水线）

    @classmethod
    def get_redis_setting(cls, pipeline: str = PIPELINE_WORKFLOW) -> RedisSettings:
        """取本流水线的 arq Redis 连接参数（redis.url 必配，缺了直接抛错，不静默连本机 db0）。"""
        yml = (cls.yml_rag if pipeline == PIPELINE_RAG else cls.yml).get("redis", {}) or {}
        settings = RedisSettings.from_dsn(yml["url"])
        if yml.get("password"):
            settings.password = yml.get("password")
        # 连接上限不写死：该段配了 max_connections 就用它，没配则保持 arq 自带默认
        if yml.get("max_connections"):
            settings.max_connections = int(yml["max_connections"])
        return settings


# 按流水线索引的生产端连接池（懒创建，进程内复用；一个进程通常只用一条流水线）
_arq_pools: dict[str, ArqRedis] = {}


async def get_arq_redis(pipeline: str = PIPELINE_WORKFLOW) -> ArqRedis:
    """获取指定流水线的 ArqRedis 生产端（首次调用时建立连接池）。"""
    pool = _arq_pools.get(pipeline)
    if pool is None:
        pool = await create_pool(CustomRedisSettings.get_redis_setting(pipeline))
        _arq_pools[pipeline] = pool
    return pool


def pipeline_cfg(pipeline: str = PIPELINE_WORKFLOW) -> tuple[str, str, str]:
    """取一条流水线的（基础队列名, 切片数 key, worker 名）；未知流水线直接报错。

    不静默回落 workflow：投错队列等于任务没人消费，而接口看上去是「提交成功」，
    这类 bug 比启动时报错难查几个量级。
    """
    cfg = _PIPELINE_CFG.get(pipeline)
    if cfg is None:
        raise ValueError(f"未注册的 arq 流水线: {pipeline}")
    return cfg


def queue_name_of(split_number: int, pipeline: str = PIPELINE_WORKFLOW) -> str:
    """拼分片队列名（生产端投递与消费端 WorkerSettings.queue_name 共用这一份拼法）。"""
    return f"{pipeline_cfg(pipeline)[0]}:{SPLIT_NAME}{split_number}"


def worker_health_key(queue_name: str, pipeline: str = PIPELINE_WORKFLOW) -> str:
    """worker 进程的健康 key，格式 = {worker_name}:{queue_name}:{hostname}:{pid}。

    queue_name 形如 rag_queue:split_2；监控端 SCAN 前缀
    f"{worker_name}:{queue_name}:" 即可枚举该队列全部 worker 节点。
    """
    return f"{pipeline_cfg(pipeline)[2]}:{queue_name}:{WORKER_ID}"


async def enqueue_job(function: str,
                      args: Optional[list] = None,
                      *,
                      job_id: Optional[str] = None,
                      split_number: int,
                      defer_until: Optional[datetime] = None,
                      pipeline: str = PIPELINE_WORKFLOW) -> bool:
    """向 arq 队列投递一个任务。

    :param function: 任务函数模块路径，必须已注册在该流水线 worker 的 WorkerSettings.functions
    :param args: 任务位置参数
    :param job_id: 全局唯一任务 ID；重复投递返回 False（幂等防重）
    :param split_number: 目标队列切片号（队列名 = {基础队列名}:split_{N}，
                         必须与 worker 端环境变量 SPLIT_NUMBER 取相同值）
    :param defer_until: 延迟到指定时刻再执行（默认立即）
    :param pipeline: 流水线（PIPELINE_WORKFLOW / PIPELINE_RAG），决定连接池与队列前缀
    :return: True=投递成功, False=重复 job_id 或投递失败
    """
    arq = await get_arq_redis(pipeline)
    queue_name = queue_name_of(split_number, pipeline)
    job = await arq.enqueue_job(
        function,
        *(args or []),
        _job_id=job_id,
        _queue_name=queue_name,
        _defer_until=defer_until,
    )
    if job is None:
        # arq 对已存在的 job_id 返回 None（任务全局去重）
        log.warning("arq enqueue skipped(duplicated job_id): {} job_id={}", function, job_id)
        return False
    log.info("arq enqueue ok: {} job_id={} queue={}", function, job_id or job.job_id, queue_name)
    return True


async def get_split_number(pipeline: str = PIPELINE_WORKFLOW) -> int:
    """读取本流水线当前的切片数配置（{基础队列名}:split_number 的值）。

    :return: 配置的切片数；key 不存在或值非法时按默认 1（与部署缺省行为一致）
    """
    arq = await get_arq_redis(pipeline)
    raw = await arq.get(pipeline_cfg(pipeline)[1])
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if MIN_SPLIT_NUMBER <= n <= MAX_SPLIT_NUMBER else 1


async def set_split_number(n: int, pipeline: str = PIPELINE_WORKFLOW) -> int:
    """写入本流水线的切片数配置（供 system 监控页修改切片数量）。

    :raises ValueError: n 超出合法范围时
    :return: 写成功的切片数
    """
    if not MIN_SPLIT_NUMBER <= n <= MAX_SPLIT_NUMBER:
        raise ValueError(f"切片数必须在 {MIN_SPLIT_NUMBER}~{MAX_SPLIT_NUMBER} 之间, 实际 {n}")
    arq = await get_arq_redis(pipeline)
    key = pipeline_cfg(pipeline)[1]
    await arq.set(key, str(n))
    log.info("arq split number set: {} = {}", key, n)
    return n


async def next_split_number(task_id: str, pipeline: str = PIPELINE_WORKFLOW) -> int:
    """按任务 ID 取模轮询选择目标切片号（同一任务稳定落同一切片队列）。

    用 zlib.crc32 而非内置 hash()：hash(str) 受 PYTHONHASHSEED 影响跨进程不稳定，
    同一 execution_id 在不同 API 进程会算出不同切片。crc32 确定且分布均匀。
    切片数来自 get_split_number()：配置 key 存在用它，否则默认 1。
    """
    n = await get_split_number(pipeline)
    return zlib.crc32(task_id.encode("utf-8")) % n + 1


async def close_arq_redis() -> None:
    """释放全部生产端连接池（服务关闭时调用；未建过的池是空操作）。"""
    for pipeline, pool in list(_arq_pools.items()):
        try:
            await pool.aclose()
            log.info("arq producer connection pool closed: {}", pipeline)
        except Exception as e:  # noqa: BLE001
            log.warning("arq producer pool close failed {}: {}", pipeline, e)
    _arq_pools.clear()
