# -*- coding: utf-8 -*-
"""kb_search 桥：挂载的知识库 → agentscope 工具（检索走 RagRetrievalService）。

双层鉴权（防「挂载后授权被收回」和「模型越白名单乱传」）：
1. 工具层：模型传的 kb_ids 必须 ⊆ 挂载白名单（不传 = 白名单全部）；
2. 服务层：RagRetrievalService.search_rows 内建 visible_ids(ACTION_USE)——
   白名单里的库授权被收回时，这条链路照样拦得住。

闭包捕获 login_user（ARQ 投递时带的 JWT 载荷 dict）与白名单——工具实例
每轮随 Toolkit 重建，快照即本轮口径。

rag 检索后端（ES/storage/rag_settings）在 worker bootstrap 里拉起；拉不起来
时 backend_ready 保持 False，工具调用返回一句「后端不可用」而不是让整轮
对话失败——未挂知识库的用户完全感知不到这段降级。

本模块只被 arq worker import（同 harness_agent 的口径）。
"""
from __future__ import annotations

from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior, PermissionContext, PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk
from pydantic import BaseModel, Field

# 检索后端可用标记：tasks/harness.py bootstrap 成功装载 rag 段后置 True
backend_ready = False

# 结果正文每条切片的截断长度：切片可能整篇几千字，kb_search 是「捞资料」
# 不是「读全文」，太长的正文堆进上下文会挤掉别的工具的输出预算
CONTENT_MAX_CHARS = 800


class KbSearchParams(BaseModel):
    """kb_search 的入参。"""

    query: str = Field(
        min_length=1, max_length=512,
        description="检索关键词，中英文均可；具体一些更容易命中。",
    )
    kb_ids: list[int] = Field(
        default_factory=list,
        description="限定在哪几个挂载的知识库里检索（留空 = 全部挂载的知识库）。",
    )
    top_k: int = Field(
        default=5, ge=1, le=20,
        description="最多返回几条切片，默认 5 条。",
    )
    with_graph: bool = Field(
        default=False,
        description="是否开启知识图谱增强（默认关）。开启后除了关键词+向量一路，还会按每"
                    "个知识库自己配置的图谱模型从问题里抽取实体和关系，再去知识图库按"
                    "邻域展开召回相关知识，与关键词+向量的结果叠加一起返回。仅对已建过图谱"
                    "的文档型知识库生效；需要关系类、跨实体问题才建议开启。",
    )
    graph_depth: int = Field(
        default=2, ge=1, le=4,
        description="图谱邻域从命中实体向外扩几跳，默认 2，最多 4（超过会按 4 取）。",
    )
    graph_scale: int = Field(
        default=20, ge=1, le=100,
        description="图谱实体/关系每一路的向量召回条数，默认 20，最多 100（超过会按 100 取）。",
    )


def _pick(row: dict, *keys: str) -> Any:
    """RetrieveHit.dump() 的字段名兼容取值（camelCase / snake_case 都认）。"""
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def make_kb_search_tool(login_user: dict, kb_ids: list[int],
                        kb_names: dict[int, str],
                        kb_descriptions: list[str] | None = None) -> ToolBase:
    """按本轮挂载白名单构造 kb_search 工具实例（闭包捕获鉴权上下文）。

    :param kb_descriptions: 各库的描述列表，拼入工具 description 让 agent 知道每个库的内容范围。
    """
    allowed = [int(i) for i in kb_ids or []]
    names = {int(k): v for k, v in (kb_names or {}).items()}

    # 工具描述：基础段 + 各库描述（让模型知道检索范围，不会放着工具不用或乱用）
    base_desc = (
        "检索当前会话挂载的知识库，返回相关切片的内容、所属文档与相似度。"
        "用于回答涉及私有资料/内部文档/已上传文件的问题——这些内容不在公开"
        "网页上，先检索再回答，不要凭记忆编造。检索不到时明确说没找到。"
    )
    if kb_descriptions:
        base_desc += "\n挂载知识库：" + "；".join(kb_descriptions)

    class KbSearch(ToolBase):
        """检索挂载的知识库（平台 RAG），返回切片正文与出处。"""

        name: str = "kb_search"

        description: str = base_desc

        input_schema: dict[str, Any] = KbSearchParams.model_json_schema()

        is_read_only: bool = True
        is_concurrency_safe: bool = True
        is_state_injected: bool = False
        is_external_tool: bool = False

        async def check_permissions(
                self,
                tool_input: dict[str, Any],
                context: PermissionContext,
        ) -> PermissionDecision:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="kb_search 按挂载白名单检索平台知识库，已鉴权。",
            )

        async def call(
                self,
                query: str,
                kb_ids: list[int] | None = None,
                top_k: int = 10,
                with_graph: bool = False,
                graph_depth: int = 2,
                graph_scale: int = 20,
        ):
            if not backend_ready:
                return ToolChunk(
                    content=[TextBlock(
                        text="知识库检索后端不可用（rag 段未配置或初始化失败），"
                             "本轮无法使用 kb_search。")],
                    state=ToolResultState.ERROR,
                )
            if not allowed:
                return ToolChunk(
                    content=[TextBlock(text="当前会话没有挂载任何知识库。")],
                    state=ToolResultState.ERROR,
                )
            # 模型传的 kb_ids 与白名单 allowed 取交集：无权的自动卸载。
            # 不传 = 全部白名单；传了但全无权 = 一个都没得查，直接结束不往下走。
            wanted = [int(i) for i in (kb_ids or [])]
            dropped = [i for i in wanted if i not in allowed]
            effective = [i for i in wanted if i in allowed]
            if not effective:
                return ToolChunk(
                    content=[TextBlock(
                        text=f"你要检索的知识库 {wanted} 都不在本轮挂载范围内（可用: {allowed}），"
                             "本次未做检索；请只检索已挂载的知识库。")],
                    state=ToolResultState.ERROR,
                )
            # 深度/规模在工具侧再夹一次，防止超大值把子图展开打爆
            # （schema 已卡 le=4/le=100，这里是双保险）
            g_depth = max(1, min(int(graph_depth or 2), 4))
            g_scale = max(1, min(int(graph_scale or 20), 100))
            try:
                from service.service_rag.services.retrieval_service import (
                    RagRetrievalService,
                )
                # graph_per_kb=True：按本轮要检索的每个库反查它自己的图谱抽取模型+实体范围分别抽词；
                # 图谱那一路不依赖本轮 agent 用的 LLM，chat_model_id 不传（各库自己定）
                # graph_source_chunks=True：把图谱命中实体回捞成 GRAPH 行，行上带实体摘要与
                # 相关三元组（A -[关系]-> B），模型才能直接引用关系而不只是看到实体名
                rows = await RagRetrievalService.search_rows(
                    login_user, kb_ids=effective, query=query, top_k=top_k,
                    with_graph=bool(with_graph), graph_depth=g_depth,
                    graph_scale=g_scale, graph_source_chunks=True, graph_per_kb=True)
            except Exception as e:  # noqa: BLE001  检索失败回给模型换路走，不炸整轮
                return ToolChunk(
                    content=[TextBlock(
                        text=f"知识库检索失败：{type(e).__name__}: {e}")],
                    state=ToolResultState.ERROR,
                )
            if not rows:
                text = (f"“{query}”没有检索到可用切片；"
                        "可以换个问法，或确认知识库已完成解析。")
                if dropped:
                    text = f"（{dropped} 不在本轮挂载范围，已跳过）{text}"
                return ToolChunk(content=[TextBlock(text=text)])
            lines = [f"“{query}”检索到 {len(rows)} 条切片"
                     f"（图谱增强{'已开' if with_graph else '未开'}）："]
            if dropped:
                lines.append(f"（{dropped} 不在本轮挂载范围，已自动跳过；"
                             f"可用: {allowed}）")
            hits = []
            graph_hits = 0
            for index, row in enumerate(rows, start=1):
                kb_id = int(_pick(row, "kbId", "kb_id") or 0)
                kb_name = names.get(kb_id) or _pick(row, "kbName", "kb_name") or kb_id
                doc_name = _pick(row, "docName", "doc_name") or "(未命名文档)"
                score = float(_pick(row, "score") or 0.0)
                recall = str(_pick(row, "recall") or "CHUNK").upper()
                graph_entity = _pick(row, "graphEntity", "graph_entity")
                graph_summary = _pick(row, "graphSummary", "graph_summary")
                graph_triples = _pick(row, "graphTriples", "graph_triples") or []
                content = str(_pick(row, "content") or "")
                if len(content) > CONTENT_MAX_CHARS:
                    content = f"{content[:CONTENT_MAX_CHARS]}…"
                is_graph = recall == "GRAPH"
                if is_graph:
                    graph_hits += 1
                tag = "（图谱命中" + (f"：{graph_entity}" if graph_entity else "") + "）" \
                    if is_graph else ""
                lines.append(f"{index}. [{kb_name} / {doc_name} / 相似度 {score:.3f}]{tag}")
                lines.append(f"   {content}")
                if is_graph and graph_summary:
                    lines.append(f"   实体摘要（{graph_entity}）：{graph_summary}")
                rel_text = "；".join(str(t) for t in graph_triples if t)
                if is_graph and rel_text:
                    lines.append(f"   相关关系：{rel_text}")
                hits.append({"kb": str(kb_name), "doc": str(doc_name),
                             "score": score, "content": content,
                             "recall": "GRAPH" if is_graph else "CHUNK",
                             "graph_entity": graph_entity,
                             "graph_summary": graph_summary,
                             "graph_triples": [str(t) for t in graph_triples if t]})
            if with_graph and not graph_hits:
                lines.append("（本轮图谱增强没有额外命中：这些库可能没建过图谱，"
                             "或问句里没出现图谱存过的实体名）")
            return ToolChunk(
                content=[TextBlock(text="\n".join(lines))],
                metadata={"results": hits},
            )

    return KbSearch()
