# -*- coding: utf-8 -*-
"""工作流执行引擎（参照 MaxKB application/flow 架构思想，FastAPI/asyncio 原生实现）。

模块组成：
- graph.py            图解析/拓扑索引/校验（MaxKB common.py 思想）
- context.py          三级上下文 + {{节点.变量}} 渲染（MaxKB reset_prompt 算法）
- engine.py           asyncio 调度器（MaxKB WorkflowManage 思想：并行/汇聚/分支路由）
- events.py           事件总线（SSE 双消费者）
- comparators.py      条件比较器（MaxKB compare/ 全集翻译）
- model_client.py     模型广场兼容层（OpenAI 兼容直连 provider）
- node_definitions.py 节点定义清单（node-definitions 接口数据源）
- templates.py        内置模板种子
- nodes/              20 种节点执行器 + 注册表
"""
