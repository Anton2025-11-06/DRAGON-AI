# -*- coding: utf-8 -*-
"""arq harness 流水线 worker 配置（深度探索智能体对话轮次的消费端）。

与 workflow / ragflow 两份 worker_settings 的关系
--------------------------------------------------
同一套 arq 机制，独立队列 harness_queue:split_{N}（互相隔离消费，SPEC §4.1
同款理由）。Redis 段优先读 Nacos 的 arq_harness；data_id 未配置时回落
arq_workflow 段（回落逻辑在 tasks/harness.py 的 prepare_config，与 API 侧
bootstrap 同一套）。

启动方式
--------
    SPLIT_NUMBER=2 arq arq_tasks.worker_settings_harness.WorkerSettingsHarness
    python -m arq_tasks.run_workers -t harness -n 2 -p 1   # 多进程由 run_workers 拉起
    scripts/run_arq_harness.sh                              # 容器与本机裸跑同一份参数

并发取值说明：每个任务 = 一条模型流 + 一个 OpenSandbox 会话 + 一份
RedisStorage 连接，比工作流节点重得多；max_jobs 给 4（对话轮次通常秒级到
分钟级，扩容靠加进程/切片而不是堆单进程并发）。
"""
import asyncio
import os

from common.common_arq.queue import (
    CustomRedisSettings, PIPELINE_HARNESS, queue_name_of, worker_health_key,
)
from arq_tasks.tasks.harness import bootstrap, prepare_config, shutdown


class WorkerSettingsHarness:
    """arq CLI 读取的 harness 流水线 worker 配置类（run_workers -t harness 映射到这里）。"""

    # 切片号由环境变量注入（run_workers -p / 容器 SPLIT_NUMBER），与生产端取相同值才对上队列
    SPLIT_NUMBER = os.environ.get("SPLIT_NUMBER", "1")

    pipeline = PIPELINE_HARNESS
    queue_name = queue_name_of(int(SPLIT_NUMBER), PIPELINE_HARNESS)
    health_check_key = worker_health_key(queue_name, PIPELINE_HARNESS)

    # 本 worker 能执行的任务函数（与 API 侧 enqueue 的 HARNESS_TASK 同一模块路径，
    # 两处不一致就是「投了没人消费」）
    functions = [
        "arq_tasks.tasks.harness.run_harness_turn",
    ]

    on_startup = bootstrap
    on_shutdown = shutdown

    # import 期先同步读一次 Nacos 拿 arq 的 Redis 配置（此时 arq 还没建 loop）：
    # 与 workflow / rag 两份同一个套路——建私有 loop、设为当前 loop、跑完
    # prepare_config 不 close，让 arq 的 Worker.__init__ 复用这个 loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(prepare_config())

    # 连接参数与生产端同源（common_arq 按流水线索引，harness 读 arq_harness 段，
    # 未配置时 get_redis_setting 回落 yml——prepare_config 已保证两侧填好）
    redis_settings = CustomRedisSettings.get_redis_setting(PIPELINE_HARNESS)

    # ---------- 并发与超时 ----------
    max_jobs = 4  # 进程内并发上限：每任务占一条模型流 + 一个沙箱会话
    # 对话轮次含分钟级沙箱命令（冷启动 bootstrap 更慢），不能被 arq 层判死，
    # 给 24h 大值；真正的挂起保护在事件循环（中断检查 + SSE 订阅兜底）
    job_timeout = 86400
    poll_delay = 0.2  # 空闲时轮询队列间隔(秒),越小任务启动越快

    # ---------- 失败与结果 ----------
    max_tries = 1  # 失败不自动重试：对话轮次非幂等，重跑会重复消耗模型额度
    retry_jobs = False  # 同上：取消/异常不重试，由消息行状态兜底
    keep_result = 0  # 结果不存 Redis(权威结果在 tb_harness_message)

    # ---------- 健康检查(供 system 模块监控接口读取) ----------
    health_check_interval = 3  # worker 心跳间隔，监控页面据此感知存活
