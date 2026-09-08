# -*- coding: utf-8 -*-
"""arq(Async Redis Queue)任务队列公共封装。

- queue.py:生产端(API 进程投递任务)与队列常量;
- 消费端(worker 进程)配置在 arq_tasks/worker_settings.py,任务实现在 arq_tasks/tasks/。
"""