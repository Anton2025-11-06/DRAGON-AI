# -*- coding: utf-8 -*-
"""arq 任务实现包。

- workflow.py:工作流执行任务 + worker 进程生命周期钩子(bootstrap/shutdown);
- ragflow.py:文档解析(解析/分块/向量化/清理)任务 + rag 流水线生命周期钩子;
- graphflow.py:知识图谱构建任务(独立 graphflow 队列，复用 ragflow 的生命周期钩子);
- 任务函数必须与各 worker_settings*.py 的 functions 注册项一一对应。
"""