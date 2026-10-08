# -*- coding: utf-8 -*-
"""arq graphflow 流水线：文档级知识图谱构建 + worker 生命周期钩子。

为什么从 ragflow 拆出来（用户反馈：图谱比文档解析流水线还耗时）
--------------------------------------------------------------
- 图谱构建要对一篇文档的**全部**可用切片逐批送大模型抽取，单篇耗时可达几十分钟到数小时，
  远长于解析/分块/向量化；和解析挤在同一队列、同一组 worker 时，几篇大文档的图谱就能把
  ``WorkerSettingsRag.max_jobs`` 的并发槽位占满，后面的文档解析只能干等；
- 拆成独立队列（graphflow_queue:split_{N}）与独立 worker 后，图谱积压不再拖慢文档解析，
  两条流水线可各自独立扩缩容（图谱慢就多起 graph worker，不必陪着加解析 worker）。

与 ragflow 的复用关系
--------------------
- 连接与后端**完全复用 ragflow 的 prepare_config/bootstrap/shutdown**：graph worker 仍从
  Nacos 的 arq_ragflow 段读 Redis 与后端配置（Neo4j/MySQL/httpx/rag_settings 同源，common_arq
  按 PIPELINE_GRAPH 映射到同一份 yml_rag），只是队列名与 worker 名独立，run_build 需要的
  依赖一件不缺；bootstrap 主体是 ragflow._bootstrap_common，仅把队列名/流水线标识换成 graphflow。
- 任务函数只负责「按 doc_id 驱动一遍图谱流水线」，真正的读切片/抽取/写 Neo4j 全在
  service.service_rag.services.graph_service 里，与 API 侧共用同一套代码。
"""
from typing import Optional

# 复用 ragflow 的生命周期钩子：graph worker 只换队列名与流水线标识，其余后端初始化与 rag
# 完全一致（rag 的 bootstrap 已抽成 _bootstrap_common）。prepare_config 读 arq_ragflow 段
# 写入 CustomRedisSettings.yml_rag，shutdown 释放同一批后端——两条流水线本就用同一份配置。
from arq_tasks.tasks.ragflow import (
    _bootstrap_common,
    prepare_config,
    shutdown,
)
from common.common_arq.queue import PIPELINE_GRAPH
from common.common_constants.rag_constant import RAG_TASK_GRAPH, rag_task_name

# graphflow 流水线注册的任务函数（worker 注册与 API 侧 enqueue 经 rag_constant 同源）
GRAPH_TASK_FUNCTIONS = [rag_task_name(RAG_TASK_GRAPH)]


async def bootstrap(ctx: dict) -> None:
    """graph worker 进程启动钩子：复用 ragflow 的公共 bootstrap，队列/流水线取 graphflow。"""
    from arq_tasks.worker_settings_graph import WorkerSettingsGraph

    await _bootstrap_common(
        ctx,
        queue_name=WorkerSettingsGraph.queue_name,
        pipeline=PIPELINE_GRAPH,
        functions=GRAPH_TASK_FUNCTIONS,
        worker_tag="graph",
    )


async def build_document_graph(ctx: dict, doc_id: int,
                               options: Optional[dict] = None) -> None:
    """文档级知识图谱构建（库级 graph_enabled 开关 + 页面手动按钮触发）。

    实体/关系写入 Neo4j 时携带 org_id/kb_id，按文档重跑先清该文档旧子图再写，保证幂等。
    ``options`` 里的 force 控制已构建过的文档要不要重抽（库开关自动触发一律不重抽）；
    ckpt 控制认不认上一轮的抽取断点（缺省 resume：失败重跑只补没抽成的批，不重烧模型调用）。
    """
    from service.service_rag.services.graph_service import RagGraphService

    await RagGraphService.run_build(doc_id, options)
