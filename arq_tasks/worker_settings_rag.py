# -*- coding: utf-8 -*-
"""arq rag 流水线 worker 配置（知识库解析/图谱/清理的消费端）。

与 workflow 那份 worker_settings.py 的关系
------------------------------------------
两类任务**不能混在一个 WorkerSettings 类上**：functions 与队列名都是类属性，混在一起
就等于让文档解析去抢工作流 worker（SPEC §4.1「两套队列完全隔离消费，互不阻塞」）。
所以这里独立一份，队列名走 ``rag_queue:split_{N}``，Redis 段读 Nacos 的 ``arq_ragflow``。

启动方式（纯 CPU，无任何 GPU 参数，SPEC §4.2/§12-4）
--------------------------------------------------
    SPLIT_NUMBER=2 arq arq_tasks.worker_settings_rag.WorkerSettingsRag
    python -m arq_tasks.run_workers -t ragflow -n 2 -p 1     # 多进程由上层入口拉起
    scripts/run_arq_ragflow.sh                                # 容器与本机裸跑同一份参数

并发与超时不写死（SPEC §13.2）
------------------------------
下面每一项都可以由 Nacos ``arq_ragflow`` 段的 ``worker:`` 子段覆盖（见 sql/init_nacos.yaml），
取值时机是模块 import 期——``prepare_config()`` 已经把那一段的字典放进
``CustomRedisSettings.yml_rag``，所以进程级参数和业务参数一样都从配置中心走，
改并发数不需要改代码重打镜像。
"""
import asyncio
import os

from common.common_arq.queue import (
    PIPELINE_RAG,
    CustomRedisSettings,
    queue_name_of,
    worker_health_key,
)
from arq_tasks.tasks.ragflow import (
    TASK_FUNCTIONS,
    bootstrap,
    prepare_config,
    shutdown,
)


class WorkerSettingsRag:
    """arq CLI 读取的 rag 流水线 worker 配置类（路径与 run_workers 的 -t ragflow 映射一致）。"""

    # 切片号由环境变量注入（run_workers -p / 容器 SPLIT_NUMBER），与生产端取相同值才对上队列
    SPLIT_NUMBER = os.environ.get("SPLIT_NUMBER", "1")

    pipeline = PIPELINE_RAG
    queue_name = queue_name_of(int(SPLIT_NUMBER), PIPELINE_RAG)
    health_check_key = worker_health_key(queue_name, PIPELINE_RAG)

    # 本 worker 能执行的任务函数：直接复用 ragflow.py 里的注册表，
    # 名字与 API 侧 enqueue 同源（两处不一致就是「投了没人消费」）
    functions = list(TASK_FUNCTIONS)

    on_startup = bootstrap
    on_shutdown = shutdown

    # import 期先同步读一次 Nacos 拿 arq 的 Redis 配置（此时 arq 还没建 loop）：
    # 与 workflow 那份同一个套路——建私有 loop、设为当前 loop、跑完 prepare_config 不 close，
    # 让 arq 的 Worker.__init__ 复用这个 loop，全进程只有一个事件循环。
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(prepare_config())

    # 连接参数与生产端同源（common_arq 按流水线索引，rag 读 arq_ragflow 段）
    redis_settings = CustomRedisSettings.get_redis_setting(PIPELINE_RAG)

    # ---------- 并发与超时 ----------
    max_jobs = 100  # 进程内并发上限(0=不限)
    job_timeout = 7200  # 2小时
    poll_delay = 0.2  # 空闲时轮询队列间隔(秒),越小任务启动越快

    # ---------- 失败与结果 ----------
    max_tries = 1  # 失败不自动重试:工作流执行非幂等,重跑会造成重复执行
    retry_jobs = False  # 同上:取消/异常不重试,由 DB 状态与快照兜底
    keep_result = 0  # 结果不存 Redis(权威结果在工作流执行记录表)

    health_check_interval = 3  # 10 秒一跳,监控页面能较实时地感知 worker 存活
