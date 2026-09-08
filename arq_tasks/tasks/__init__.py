# -*- coding: utf-8 -*-
"""arq 任务实现包。

- workflow.py:工作流执行/恢复任务 + worker 进程生命周期钩子(bootstrap/shutdown);
- 任务函数必须与 worker_settings.py 的 functions 注册项一一对应。
"""