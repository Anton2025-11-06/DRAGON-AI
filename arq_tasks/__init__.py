# -*- coding: utf-8 -*-
"""arq 任务队列包(替代 celery_tasks)。

arq 全名 Async Redis Queue:
- worker 进程本身就是 asyncio 事件循环,任务函数直接写 async def,无需线程适配层;
- 任务通过 Redis(ZSET 按时间排序)传递,同一队列可挂多个 worker 进程竞争消费;
- 关键配置见 worker_settings.py(队列名/Redis db/并发上限等)。
"""