# -*- coding: utf-8 -*-
"""arq graphflow 流水线 worker 配置（文档级知识图谱构建的独立消费端）。

为什么单独一条流水线（而不是并进 worker_settings_rag）
------------------------------------------------------
图谱抽取要对一篇文档的全部切片逐批走大模型，单篇耗时远长于解析/分块/向量化；若和解析
共用一个 WorkerSettings，几篇大文档的图谱就会把 max_jobs 槽位占满，后面的文档解析只能干等。
拆成 graphflow 后队列名与 worker 名各自独立（graphflow_queue:split_{N} / graphflow_worker），
图谱积压不再拖慢文档解析，两条流水线可独立扩缩容。

连接与生命周期钩子完全复用 ragflow
----------------------------------
Redis 段仍读 Nacos 的 arq_ragflow（common_arq 把 PIPELINE_GRAPH 映射到同一份 yml_rag），
prepare_config/bootstrap/shutdown 与 rag 是同一套实现，只是队列与流水线标识换成 graphflow，
run_build 需要的 Neo4j / 业务 Redis / httpx / rag_settings 一件不缺。

启动方式（纯 CPU + 大模型 HTTP 调用，无任何 GPU 参数）
----------------------------------------------------
    SPLIT_NUMBER=1 arq arq_tasks.worker_settings_graph.WorkerSettingsGraph
    python -m arq_tasks.run_workers -t graphflow -n 2 -p 1     # 多进程由上层入口拉起
    scripts/run_arq_graphflow.sh                                # 容器与本机裸跑同一份参数
"""
import asyncio
import os

from common.common_arq.queue import (
    PIPELINE_GRAPH,
    CustomRedisSettings,
    queue_name_of,
    worker_health_key,
)
from arq_tasks.tasks.graphflow import (
    GRAPH_TASK_FUNCTIONS,
    bootstrap,
    prepare_config,
    shutdown,
)


class WorkerSettingsGraph:
    """arq CLI 读取的 graphflow 流水线 worker 配置类（路径与 run_workers 的 -t graphflow 映射一致）。"""

    # 切片号由环境变量注入（run_workers -p / 容器 SPLIT_NUMBER），与生产端取相同值才对上队列
    SPLIT_NUMBER = os.environ.get("SPLIT_NUMBER", "1")

    pipeline = PIPELINE_GRAPH
    queue_name = queue_name_of(int(SPLIT_NUMBER), PIPELINE_GRAPH)
    health_check_key = worker_health_key(queue_name, PIPELINE_GRAPH)

    # 只注册图谱构建任务：文档解析仍归 WorkerSettingsRag，两边队列完全隔离互不消费
    functions = list(GRAPH_TASK_FUNCTIONS)

    on_startup = bootstrap
    on_shutdown = shutdown

    # import 期先同步读一次 Nacos 拿 arq 的 Redis 配置（graph 复用 arq_ragflow 段）：
    # 与 rag 那份同一个套路——建私有 loop、设为当前 loop、跑完 prepare_config 不 close，
    # 让 arq 的 Worker.__init__ 复用这个 loop，全进程只有一个事件循环。
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(prepare_config())

    # 连接参数与生产端同源（common_arq 按流水线索引，graph 与 rag 都读 arq_ragflow 段）
    redis_settings = CustomRedisSettings.get_redis_setting(PIPELINE_GRAPH)

    # ---------- 并发与超时 ----------
    # 图谱是这批任务里最慢的一类：单篇可能几十分钟到数小时，job_timeout 给到 4 小时
    # （比解析的 2 小时更长），免得被 arq 层中途判死而留下一半已写进 Neo4j 的子图。
    max_jobs = 100  # 进程内并发上限(0=不限)
    job_timeout = 14400  # 4小时
    poll_delay = 0.2  # 空闲时轮询队列间隔(秒)

    # ---------- 失败与结果 ----------
    max_tries = 1  # 失败不自动重试:图谱重抽是新一轮大模型调用,由页面按钮显式触发
    retry_jobs = False  # 同上:异常不重试,graph_state 落 FAILED 由页面回查原因
    keep_result = 0  # 结果不存 Redis(权威状态在 tb_document.graph_state)

    health_check_interval = 3  # 3 秒一跳,监控页面能较实时地感知 worker 存活
