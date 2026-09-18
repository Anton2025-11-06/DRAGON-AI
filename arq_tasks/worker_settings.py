# -*- coding: utf-8 -*-
"""arq worker 全局配置(所有切片共用这一个 WorkerSettings 类)。

部署方式:队列按切片号分片,worker 用环境变量 SPLIT_NUMBER 指定消费哪个切片:
    SPLIT_NUMBER=2 arq arq_tasks.worker_settings.WorkerSettings
多进程扩展:arq CLI 本身单进程(无 -w/--workers 参数),用上层入口拉起 N 个进程:
    python -m arq_tasks.run_workers -n 4 -p 2
本文件所有配置均显式写明取值与原因,便于后续维护调整。
"""
import asyncio
import os
from arq.connections import RedisSettings

from arq_tasks.tasks.workflow import shutdown, bootstrap, prepare_config
from common.common_arq.queue import worker_health_key, SPLIT_NAME, QUEUE_NAME


class WorkerSettings:
    """arq CLI 读取的 worker 配置类(类名固定,被 `arq x.WorkerSettings` 引用)。"""

    SPLIT_NUMBER = os.environ.get('SPLIT_NUMBER', '1')

    # 队列名: 分片队列
    queue_name = f"{QUEUE_NAME}:{SPLIT_NAME}{SPLIT_NUMBER}"

    health_check_key = worker_health_key(queue_name)

    # 本 worker 能执行的任务函数(按模块路径注册,worker 进程按名解析)
    # 只留 execute_workflow:首跑与再提交(重新执行/审批后恢复)共用它,resume_workflow
    # 随外部暂停链路一起废弃(保留会让旧投递的 job 找不到函数名而报错)
    functions = [
        "arq_tasks.tasks.workflow.execute_workflow",
    ]

    on_startup = bootstrap
    on_shutdown = shutdown

    # worker_settings.py 模块级（import 期执行，此时 arq 还没建 loop）
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)  # 关键：不设的话 arq 会另建一个 loop
    _loop.run_until_complete(prepare_config())  # 只 await 读 Nacos → CustomRedisSettings.yml = ...

    # 不 close：arq 的 Worker.__init__ 走 get_event_loop() 拿到的就是这个 loop，全进程同一个 loop
    # redis 连接参数(与生产端同源,保证投递/消费指向同一 Redis)
    from common.common_arq.queue import CustomRedisSettings
    redis_settings = CustomRedisSettings.get_redis_setting()

    # ---------- 并发与超时 ----------
    max_jobs = 100  # 进程内并发上限(0=不限)
    # arq 0.28 的 job_timeout 语义:值为 0 会被 asyncio.wait_for(task, 0) 立即判超时
    # (不是"禁用超时"!),默认 300s。PPT 等长任务不能被 arq 层中途判死,故给 24h 大值;
    # 真正的挂起保护在 engine 层(节点级 timeout,默认 600s),绝不会永久悬挂。
    job_timeout = 86400
    poll_delay = 0.2  # 空闲时轮询队列间隔(秒),越小任务启动越快

    # ---------- 失败与结果 ----------
    max_tries = 1  # 失败不自动重试:工作流执行非幂等,重跑会造成重复执行
    retry_jobs = False  # 同上:取消/异常不重试,由 DB 状态与快照兜底
    keep_result = 0  # 结果不存 Redis(权威结果在工作流执行记录表)

    # ---------- 健康检查(供 system 模块监控接口读取) ----------
    # worker 每 health_check_interval 秒写一次节点级健康 key
    # (key = {queue_name}:workers:{hostname}:{pid},TTL = interval + 1 秒),
    # 值形如 "Sep-08 12:00:00 j_complete=3 j_failed=0 j_retried=0 j_ongoing=1 queued=2";
    # 每个 worker 一个 key(hostname:pid 唯一),监控端可枚举「有哪些节点存活」。
    health_check_interval = 3  # 10 秒一跳,监控页面能较实时地感知 worker 存活
