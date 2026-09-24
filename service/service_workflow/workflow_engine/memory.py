# -*- coding: utf-8 -*-
"""大模型节点记忆（需求 1）：跨轮对话历史的存储、聚合、截断与注入形态。

设计口径（docs/workflow-approval-memory.md 第 8 节）：
- 权威存储 = `node_states[node_id].llmMessages` = `[{role, content, round, ts}]`，
  不新开列，随节点状态 JSON 一起落库/恢复（记忆与节点状态同生命周期，天然按
  node_id 隔离，也不需要在明细表里另存一份）；
- 条数语义 = **消息条数**（一轮 user+assistant = 2 条），单条 content 超长截断；
- 注入形态按模型能力类型分两类：文生文插 messages 段（system 之后、本轮 user 之前），
  有文本提示词槽的类型拼成「对话记录前缀」进 prompt —— 两者都是**运行时动态拼装**，
  绝不回写节点 config.input（否则下一轮的记忆会把上一轮拼进去的历史再拼一遍，指数膨胀）；
- 向量/重排/音频转文字/文生音频不提供记忆：前两类加历史会污染表征与打分，
  后两类没有可注入的对话文本槽（ASR 输入只有音频，TTS 的 text 是「要念出来的内容」，
  拼历史会让模型把整段对话朗读者念出来）。

本模块是纯函数集合（除 time 外无 IO），便于单测；模型调用（COMPRESS 策略）
由调用方传入 async 回调，避免这里依赖 WorkflowModelClient 造成循环 import。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from common.common_constants.model_constant import (
    MT_AUDIO_TO_TEXT, MT_IMAGE_EMBEDDING, MT_IMAGE_TO_VIDEO, MT_IMAGE_UNDERSTAND,
    MT_MULTIMODAL_EMBEDDING, MT_OCR, MT_TEXT_EMBEDDING, MT_TEXT_RERANK,
    MT_TEXT_TO_AUDIO, MT_TEXT_TO_IMAGE, MT_TEXT_TO_VIDEO, MT_TEXT_TO_TEXT,
    MT_VIDEO_UNDERSTAND,
)
from service.service_workflow.workflow_engine.model_client import ChatMessage

# ==================== 配置常量 ====================

MEMORY_SCOPES = ("SELF", "NODES", "WORKFLOW")
MEMORY_STRATEGIES = ("DROP_OLDEST", "DROP_MIDDLE", "DROP_NEWEST", "COMPRESS")

MEMORY_LIMIT_DEFAULT = 1
MEMORY_LIMIT_MAX = 100
# 单条消息字符上限：一次检索结果/长文回答整段塞进记忆会把 node_states JSON 撑爆
MEMORY_CONTENT_MAX = 4000
# 存储侧硬上限：与配置上限解耦（改小 memoryLimit 只影响注入条数，不影响已存历史）
MEMORY_STORE_MAX = MEMORY_LIMIT_MAX

# 文生文：历史作为独立 messages 段
MEMORY_MESSAGE_CATEGORY = MT_TEXT_TO_TEXT
# 有文本提示词槽、可拼「对话记录前缀」的能力类型
MEMORY_PROMPT_CATEGORIES = (
    MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR,
    MT_TEXT_TO_IMAGE, MT_TEXT_TO_VIDEO, MT_IMAGE_TO_VIDEO,
)
# 表单不展示 + validate ERROR 的能力类型
MEMORY_DISABLED_CATEGORIES = (
    MT_TEXT_EMBEDDING, MT_IMAGE_EMBEDDING, MT_MULTIMODAL_EMBEDDING, MT_TEXT_RERANK,
    MT_AUDIO_TO_TEXT, MT_TEXT_TO_AUDIO,
)

# 可注入 = 对话族 + 提示词族（其余一律视为不支持）
MEMORY_SUPPORT_CATEGORIES = (MEMORY_MESSAGE_CATEGORY,) + MEMORY_PROMPT_CATEGORIES

_HISTORY_HEADER = "以下是本次会话此前的对话记录（仅供理解上下文，不要直接复述）："


def supports_category(category: str) -> bool:
    return category in MEMORY_SUPPORT_CATEGORIES


def is_compound_body(graph, node_id: str) -> bool:
    """节点是否落在 LOOP/PARALLEL 的子图里（子图内记忆每轮覆盖）。

    子图成员关系靠拓扑算（画布没有父子字段），每次模型调用都重算一遍太浪费，
    结果缓存在图上（graph 本身与图数据同生命周期，不会失效）。
    """
    if graph is None:
        return False
    cache = getattr(graph, "_compound_body_cache", None)
    if cache is None:
        cache = set(graph.compound_body_node_ids() or set())
        graph._compound_body_cache = cache
    return node_id in cache


@dataclass
class MemoryConfig:
    """节点记忆配置（已归一，字段语义见前端表单）。"""

    limit: int = MEMORY_LIMIT_DEFAULT
    scope: str = "SELF"
    nodes: list = field(default_factory=list)
    strategy: str = "DROP_OLDEST"
    compress_model_id: Optional[int] = None


def resolve_config(cfg: dict) -> Optional[MemoryConfig]:
    """读节点 data 里的记忆配置；未开启返回 None（None 而非对象，调用处少一层判断）。"""
    if not cfg or cfg.get("memoryEnabled") is not True:
        return None
    try:
        limit = int(cfg.get("memoryLimit") or MEMORY_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = MEMORY_LIMIT_DEFAULT
    limit = max(1, min(limit, MEMORY_LIMIT_MAX))
    scope = str(cfg.get("memoryScope") or "SELF").upper()
    if scope not in MEMORY_SCOPES:
        scope = "SELF"
    strategy = str(cfg.get("memoryStrategy") or "DROP_OLDEST").upper()
    if strategy not in MEMORY_STRATEGIES:
        strategy = "DROP_OLDEST"
    raw_nodes = cfg.get("memoryNodes") or []
    if isinstance(raw_nodes, str):
        raw_nodes = [raw_nodes]
    nodes = [str(n) for n in raw_nodes if n]
    if scope != "NODES":
        nodes = []
    compress_model_id = cfg.get("memoryCompressModelId")
    return MemoryConfig(
        limit=limit, scope=scope, nodes=nodes, strategy=strategy,
        compress_model_id=int(compress_model_id) if compress_model_id else None)


# ==================== 存储 ====================


def truncate_content(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= MEMORY_CONTENT_MAX:
        return text
    return text[:MEMORY_CONTENT_MAX] + "...(已截断)"


def append_round(state, round_no: int, user_text: Optional[str],
                 assistant_text: Optional[str], overwrite: bool = False) -> None:
    """把本轮的一对消息写进 NodeState.llmMessages。

    round_no 来自 runtime（execution 即会话，每次 RETRY 提交 +1）；缺省/非法时
    退化为「已有最大 round + 1」，保证同一节点内单调递增——排序键坏掉的话，
    WORKFLOW 范围聚合出来的对话顺序就会乱。

    overwrite：LOOP 体内的节点用覆写（一次循环里同一节点跑 N 轮，
    追加会把额度刷完）；主干节点始终追加。
    """
    if state is None:
        return
    items = [m for m in (state.llmMessages or []) if isinstance(m, dict)]
    if overwrite:
        items = []
    try:
        rnd = int(round_no or 0)
    except (TypeError, ValueError):
        rnd = 0
    if rnd <= 0:
        rnd = max((int(m.get("round") or 0) for m in items), default=0) + 1
    ts = int(time.time())
    for role, text in (("user", user_text), ("assistant", assistant_text)):
        content = truncate_content(text) if text is not None else ""
        if not content:
            continue  # 空轮不占条数额度（否则 limit=1 会只剩一条 system 摘要）
        items.append({"role": role, "content": content, "round": rnd, "ts": ts})
    state.llmMessages = items[-MEMORY_STORE_MAX:]


# ==================== 聚合 ====================


def collect(runtime, node_id: str, mc: MemoryConfig) -> list[dict]:
    """按配置范围取候选历史，统一按 `round → order → 写入序` 排序。

    跨节点聚合必须有一个稳定的全序：round 区分对话轮次，order 是引擎的全局调度序号
    （恢复时由 hydrate 从历史最大值续编，因此跨轮也单调），写入序处理同一节点的
    user/assistant 相邻对。
    """
    if mc.scope == "SELF":
        ids = [node_id]
    elif mc.scope == "NODES":
        ids = list(mc.nodes)
    else:
        ids = list(runtime.node_states.keys())
    items: list[dict] = []
    for nid in ids:
        state = runtime.node_states.get(nid)
        if state is None:
            continue
        for seq, msg in enumerate(state.llmMessages or []):
            if not isinstance(msg, dict):
                continue

            def _num(v, default=0):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return default

            items.append({"role": msg.get("role") or "user",
                          "content": truncate_content(msg.get("content")),
                          "_round": _num(msg.get("round")),
                          "_order": _num(state.order), "_seq": seq})
    items.sort(key=lambda m: (m["_round"], m["_order"], m["_seq"]))
    return items


def _head_tail(msgs: list[dict], limit: int) -> list[dict]:
    """DROP_MIDDLE：对半保留最旧与最新，砍掉中间。"""
    head = limit // 2
    tail = limit - head
    return msgs[:head] + (msgs[-tail:] if tail else [])


def apply_limit(msgs: list[dict], mc: MemoryConfig) -> tuple[list[dict], list[dict]]:
    """按策略截断。

    :return: (注入用的消息列表, 被丢弃 oldest 部分[供 COMPRESS 摘要])
    """
    if len(msgs) <= mc.limit:
        return msgs, []
    if mc.strategy == "DROP_NEWEST":
        return msgs[:mc.limit], []
    if mc.strategy == "DROP_MIDDLE":
        return _head_tail(msgs, mc.limit), []
    if mc.strategy == "COMPRESS":
        # 摘要占 1 个额度，其余留给最新的历史：这样总条数恒等于 limit
        keep = max(0, mc.limit - 1)
        tail = msgs[-keep:] if keep else []
        return tail, msgs[:len(msgs) - len(tail)]
    # DROP_OLDEST（默认）
    return msgs[-mc.limit:], []


# ==================== 渲染（注入形态） ====================


def to_chat_messages(msgs: list[dict], summary: Optional[str] = None) -> list[ChatMessage]:
    """转成对话族 messages：摘要作为 system 级前置，其余按原 role（未知 role 归一为 user）。"""
    out: list[ChatMessage] = []
    if summary:
        out.append(ChatMessage(role="system", content=summary))
    for m in msgs:
        role = str(m.get("role") or "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        out.append(ChatMessage(role=role, content=m.get("content") or ""))
    return out


def render_prefix(msgs: list[dict], summary: Optional[str] = None) -> str:
    """非对话族：历史压成「对话记录前缀」文本（无历史时返回空串，便于 `if prefix`）。"""
    lines: list[str] = []
    if summary:
        lines.append(summary)
    for m in msgs:
        role = "用户" if m.get("role") == "user" else (
            "助手" if m.get("role") == "assistant" else "系统")
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"[{role}] {content}")
    if not lines:
        return ""
    return _HISTORY_HEADER + "\n" + "\n".join(lines)


def render_prompt(prefix: str, prompt: str) -> str:
    """拼进 prompt：前缀在上、本轮提示词在下（模型对尾部指令更敏感）。"""
    if not prefix:
        return prompt or ""
    return f"{prefix}\n\n{prompt}" if prompt else prefix


def compress_instruction(text: str) -> str:
    return (
        "请把下面的对话记录压缩成一段简要摘要，保留：话题、用户的关键诉求与偏好、"
        "已确认的事实与结论、未解决的问题。不要添加评价，不要编造内容。\n\n"
        f"{text}")


def history_text(msgs: list[dict]) -> str:
    """供摘要用的纯文本对话记录（不带前缀标题）。"""
    lines = []
    for m in msgs:
        role = "用户" if m.get("role") == "user" else (
            "助手" if m.get("role") == "assistant" else "系统")
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


# ==================== 静态校验 ====================


def validate_memory(node, graph, model_categories: Optional[dict] = None) -> list:
    """LLM 节点记忆配置校验（由 `WorkflowGraph.validate` 统一调用）。

    为什么不放进 `LLMNodeExecutor.validate_node`：「哪个能力类型能不能用记忆」取决于
    所选模型的 category，而 category 要查库；节点级 validate_node 是纯函数协议
    （只有 node + graph），拿不到模型信息。模型不可用时（model_categories 为 None）
    只做形式校验，不报类型错——不能因为查不到模型就把用户的图卡住发布。
    """
    from service.service_workflow.workflow_engine.nodes.base import issue

    data = node.data or {}
    if data.get("memoryEnabled") is not True:
        return []
    issues: list = []
    model_id = data.get("modelId")
    category = (model_categories or {}).get(str(model_id))
    if category and not supports_category(category):
        issues.append(issue("LLM_MEMORY_CATEGORY", "ERROR",
                            f"记忆不适用于当前模型能力类型（{category}）",
                            node,
                            suggestion="向量/重排/语音识别/语音合成类模型不要开记忆："
                                       "前两类会被历史污染表征，后两类没有可注入的对话文本槽"))
    try:
        limit = int(data.get("memoryLimit") or MEMORY_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = 0
    if not 1 <= limit <= MEMORY_LIMIT_MAX:
        issues.append(issue("LLM_MEMORY_LIMIT", "ERROR",
                            f"记忆条数非法：{data.get('memoryLimit')}，有界范围 1~{MEMORY_LIMIT_MAX}", node))
    scope = str(data.get("memoryScope") or "SELF").upper()
    if scope not in MEMORY_SCOPES:
        issues.append(issue("LLM_MEMORY_SCOPE", "ERROR", f"记忆范围取值非法：{scope}", node))
    strategy = str(data.get("memoryStrategy") or "DROP_OLDEST").upper()
    if strategy not in MEMORY_STRATEGIES:
        issues.append(issue("LLM_MEMORY_STRATEGY", "ERROR", f"记忆策略取值非法：{strategy}", node))
    if scope == "NODES":
        raw = data.get("memoryNodes") or []
        ids = [raw] if isinstance(raw, str) else list(raw)
        if not [i for i in ids if i]:
            issues.append(issue("LLM_MEMORY_NODES", "ERROR",
                                "记忆范围选了「指定节点」但未选择任何节点", node))
        for nid in ids:
            if not nid:
                continue
            target = graph.get_node(str(nid)) if graph is not None else None
            if target is None:
                issues.append(issue("LLM_MEMORY_NODE_MISSING", "ERROR",
                                    f"记忆引用了不存在的节点：{nid}", node))
            elif target.type != "LLM":
                issues.append(issue("LLM_MEMORY_NODE_TYPE", "SUGGESTION",
                                    f"记忆引用的「{target.label}」不是大模型节点，不会产生历史",
                                    node))
    if strategy == "COMPRESS":
        compress_id = data.get("memoryCompressModelId")
        if not compress_id:
            issues.append(issue("LLM_MEMORY_NO_COMPRESS_MODEL", "ERROR",
                                "记忆策略选了「自动压缩」但未选择压缩模型", node,
                                suggestion="选一个文生文模型用于生成历史摘要"))
        else:
            compress_cat = (model_categories or {}).get(str(compress_id))
            if compress_cat and compress_cat != MEMORY_MESSAGE_CATEGORY:
                issues.append(issue("LLM_MEMORY_COMPRESS_MODEL", "ERROR",
                                    f"记忆压缩模型必须是文生文模型，当前为 {compress_cat}", node))
    return issues


async def build_injection(runtime, node_id: str, cfg: dict, category: str,
                          chat_call: Optional[Callable[[str], Awaitable[str]]] = None,
                          ) -> tuple[list[ChatMessage], str, Optional[str]]:
    """执行前的统一入口：算出本轮要注入的历史。

    :param chat_call: COMPRESS 策略用的摘要调用，`async (prompt) -> 摘要文本`；
                      未提供或调用失败时自动降级 DROP_OLDEST。
    :return: (对话族 messages, 非对话族 prompt 前缀, 降级告警文案或 None)
    """
    mc = resolve_config(cfg)
    if mc is None or not supports_category(category):
        return [], "", None
    msgs = collect(runtime, node_id, mc)
    warning: Optional[str] = None
    summary: Optional[str] = None
    kept, dropped = apply_limit(msgs, mc)
    if mc.strategy == "COMPRESS" and dropped:
        if chat_call is None:
            warning = "记忆压缩未配置可用模型，已改为丢弃最旧记忆"
        else:
            try:
                text = await chat_call(compress_instruction(history_text(dropped)))
                summary = (text or "").strip() or None
                if summary is None:
                    warning = "记忆压缩返回空，已改为丢弃最旧记忆"
            except Exception as e:  # noqa: BLE001
                # 记忆是增强项，不能因为摘要失败把整个节点弄挂
                warning = f"记忆压缩失败，已改为丢弃最旧记忆: {e}"
        if warning:
            # 降级：没摘要可用就只保留最新 limit 条（COMPRESS 的「摘要+较新历史」额度作废）
            kept = msgs[-mc.limit:] if len(msgs) > mc.limit else msgs
            summary = None
    if category == MEMORY_MESSAGE_CATEGORY:
        return to_chat_messages(kept, summary), "", warning
    return [], render_prefix(kept, summary), warning
