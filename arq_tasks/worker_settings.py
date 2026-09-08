# -*- coding: utf-8 -*-
"""arq worker 全局配置(所有队列组共用这一个 WorkerSettings 类)。

部署方式:同一份代码,用环境变量 ARQ_QUEUE 区分启动哪个队列组的 worker:
    ARQ_QUEUE=workflow arq arq_tasks.worker_settings.WorkerSettings
按需再起 clean/math/email 等其他组(本仓库现阶段只有 workflow 组)。

本文件所有配置均显式写明取值与原因,便于后续维护调整。
"""
import os

from arq.connections import RedisSettings

from arq_tasks.tasks.workflow import bootstrap, shutdown
from common.common_arq.queue import ARQ_REDIS_DSN, worker_health_key


class WorkerSettings:
    """arq CLI 读取的 worker 配置类(类名固定,被 `arq x.WorkerSettings` 引用)。"""

    # 队列名:同名 worker 消费同一个 Redis ZSET,多进程即分布式扩展
    queue_name = os.environ.get("ARQ_QUEUE", "workflow")

    # 节点级健康 key(改造:每个 worker 进程唯一,hostname:pid 跨机唯一):
    # arq 内置 record_health 每 health_check_interval 秒写一次该 key(TTL=interval+1,
    # psetex 自动续期),worker 退出时 arq 自动删除自己的 key(优雅下线);
    # 失联(kill -9)节点 key 在 TTL 内自然过期消失 → 监控端 SCAN 前缀即可枚举存活节点。
    # 同机 pid 复用残留由 tasks/workflow.bootstrap 启动时 DEL 兜底。
    health_check_key = worker_health_key(queue_name)

    # 本 worker 能执行的任务函数(按模块路径注册,worker 进程按名解析)
    functions = [
        "arq_tasks.tasks.workflow.execute_workflow",
        "arq_tasks.tasks.workflow.resume_workflow",
    ]

    # redis 连接参数(db=1 为需求约定)
    redis_settings = RedisSettings.from_dsn(ARQ_REDIS_DSN)

    # 进程启动/退出钩子(每 worker 进程各执行一次,替代旧 celery 方案的每任务幂等 bootstrap)
    # 注意:arq 0.28 的 on_startup/on_shutdown 必须传可调用对象(只有 functions 支持字符串
    # 路径按名解析,钩子直接 self.on_startup(self.ctx) 调用,传字符串会报 'str' object is not callable)
    on_startup = bootstrap
    on_shutdown = shutdown

    # ---------- 并发与超时 ----------
    max_jobs = int(os.environ.get("ARQ_MAX_JOBS", "50"))   # 进程内并发上限(0=不限)
    # arq 0.28 的 job_timeout 语义:值为 0 会被 asyncio.wait_for(task, 0) 立即判超时
    # (不是"禁用超时"!),默认 300s。PPT 等长任务不能被 arq 层中途判死,故给 24h 大值;
    # 真正的挂起保护在 engine 层(节点级 timeout,默认 600s),绝不会永久悬挂。
    job_timeout = 86400
    poll_delay = 1         # 空闲时轮询队列间隔(秒),越小任务启动越快

    # ---------- 失败与结果 ----------
    max_tries = 1            # 失败不自动重试:工作流执行非幂等,重跑会造成重复执行
    retry_jobs = False       # 同上:取消/异常不重试,由 DB 状态与快照兜底
    keep_result = 0          # 结果不存 Redis(权威结果在工作流执行记录表)

    # ---------- 健康检查(供 system 模块监控接口读取) ----------
    # worker 每 health_check_interval 秒写一次节点级健康 key
    # (key = {queue_name}:workers:{hostname}:{pid},TTL = interval + 1 秒),
    # 值形如 "Sep-08 12:00:00 j_complete=3 j_failed=0 j_retried=0 j_ongoing=1 queued=2";
    # 每个 worker 一个 key(hostname:pid 唯一),监控端可枚举「有哪些节点存活」。
    health_check_interval = 10   # 10 秒一跳,监控页面能较实时地感知 worker 存活