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
from agentscope.tool import ToolBase
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


def _pick(row: dict, *keys: str) -> Any:
    """RetrieveHit.dump() 的字段名兼容取值（camelCase / snake_case 都认）。"""
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def make_kb_search_tool(login_user: dict, kb_ids: list[int],
                        kb_names: dict[int, str]) -> ToolBase:
    """按本轮挂载白名单构造 kb_search 工具实例（闭包捕获鉴权上下文）。"""
    allowed = [int(i) for i in kb_ids or []]
    names = {int(k): v for k, v in (kb_names or {}).items()}

    class KbSearch(ToolBase):
        """检索挂载的知识库（平台 RAG），返回切片正文与出处。"""

        name: str = "kb_search"

        description: str = (
            "检索当前会话挂载的知识库，返回相关切片的内容、所属文档与相似度。"
            "用于回答涉及私有资料/内部文档/已上传文件的问题——这些内容不在公开"
            "网页上，先检索再回答，不要凭记忆编造。检索不到时明确说没找到。"
        )

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
            top_k: int = 5,
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
            # 模型传入的库清单必须落在白名单内（不传 = 全部白名单）
            wanted = [int(i) for i in (kb_ids or [])]
            illegal = [i for i in wanted if i not in allowed]
            if illegal:
                return ToolChunk(
                    content=[TextBlock(
                        text=f"以下知识库不在本轮挂载范围内: {illegal}"
                             f"（可用: {allowed}）")],
                    state=ToolResultState.ERROR,
                )
            effective = wanted or allowed
            try:
                from service.service_rag.services.retrieval_service import (
                    RagRetrievalService,
                )
                rows = await RagRetrievalService.search_rows(
                    login_user, kb_ids=effective, query=query, top_k=top_k)
            except Exception as e:  # noqa: BLE001  检索失败回给模型换路走，不炸整轮
                return ToolChunk(
                    content=[TextBlock(
                        text=f"知识库检索失败：{type(e).__name__}: {e}")],
                    state=ToolResultState.ERROR,
                )
            if not rows:
                return ToolChunk(
                    content=[TextBlock(
                        text=f"“{query}”没有检索到可用切片；"
                             "可以换个说法，或确认知识库已完成解析。")],
                )
            lines = [f"“{query}”检索到 {len(rows)} 条切片："]
            hits = []
            for index, row in enumerate(rows, start=1):
                kb_id = int(_pick(row, "kbId", "kb_id") or 0)
                kb_name = names.get(kb_id) or _pick(row, "kbName", "kb_name") or kb_id
                doc_name = _pick(row, "docName", "doc_name") or "(未命名文档)"
                score = float(_pick(row, "score") or 0.0)
                content = str(_pick(row, "content") or "")
                if len(content) > CONTENT_MAX_CHARS:
                    content = f"{content[:CONTENT_MAX_CHARS]}…"
                lines.append(f"{index}. [{kb_name} / {doc_name} / 相似度 {score:.3f}]")
                lines.append(f"   {content}")
                hits.append({"kb": str(kb_name), "doc": str(doc_name),
                             "score": score, "content": content})
            return ToolChunk(
                content=[TextBlock(text="\n".join(lines))],
                metadata={"results": hits},
            )

    return KbSearch()
