# -*- coding: utf-8 -*-
"""arq harness 流水线：深度探索智能体一轮对话（run_harness_turn）+ worker 生命周期钩子。

一轮任务的完整链路（与 workflow 的 execute_workflow 同一个「跨进程交接只有
MySQL 那一行」模式）：
1. 状态守卫：turn 快照不存在或非 RUNNING 直接退出（幂等投递残留 / 用户已中断）；
2. 恢复 AgentState（agentscope RedisStorage：user=user_id、agent=harness_agent、
   session=会话 id——跨轮记忆窗口与 HITL 待答上下文都在这份 state 里）；
3. 组装：model_factory（模型广场授权）→ workspace_factory（attach/建沙箱）→
   harness_agent（权限策略 + Toolkit + Agent）；
4. 事件循环：reply_stream 的每个事件 publish 到 harness:evt:{turn_id}，同时
   累积 text/thinking/tool_calls（终态落库用）；事件间隙按时间窗查 DB 状态，
   INTERRUPTED 即收尾退出（interrupt 端点置的状态）；
5. 收尾：finish_turn 终态回写 + 终态事件 + upsert state（沙箱由 workspace_scope
   的 async with 退出时自动 close_all 释放，不再手工 _release_workspace）
   （OpenSandboxWorkspace 没有 pause()：沙箱靠服务端 timeout_seconds 自然暂停，
   文件系统保留，下轮 initialize 按 workspace_id reattach+resume）。

M3 HITL 恢复已落地：每轮输入前先查 state 的挂起待答（_hitl_resume_inputs
把本轮用户消息转成 ExternalExecutionResultEvent / UserConfirmResultEvent
续跑），一轮答一个挂起，多个挂起逐轮收敛；挂起收尾时把问题文本随
turn.interrupted 帧下发（question 字段）。用户中断在事件间隙检出后用
UserInterruptEvent 闭合未完成的工具调用再收尾（demo 同款语义）。
"""
import asyncio
import logging
import os
import sys
from urllib.parse import quote_plus

from common.common_arq.queue import (
    CustomRedisSettings, PIPELINE_HARNESS, worker_health_key,
)
from common.common_constants.constant import (
    ARQ_HARNESS, ARQ_RAGFLOW, ARQ_WORKFLOW, SERVICE_HARNESS,
)
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_nacos.config import Config
from common.common_nacos.nacos_client import nacos_client
from common.common_redis import redis

from service.service_harness.models.harness_entity import (
    TURN_COMPLETED, TURN_FAILED, TURN_INTERRUPTED, TURN_RUNNING,
)
from service.service_harness.services.harness_pubsub import (
    EVT_TEXT_DELTA, EVT_THINKING_DELTA, EVT_TOOL_COMPLETED, EVT_TOOL_STARTED,
    EVT_TURN_COMPLETED, EVT_TURN_FAILED, EVT_TURN_INTERRUPTED,
    publish_turn_event,
)
# 帧协议说明：终态帧（turn.completed / turn.interrupted / turn.failed）在本文件
# 里只有一个出口——run_harness_turn 收尾处。reply_stream 循环内不发终态帧
# （ReplyEndEvent 的 finished_reason 可能是挂起，提前发 completed 会让前端
# 在挂起轮错误关流）
from service.service_harness.services.session_service import HarnessSessionService


async def prepare_config():
    """worker_settings_harness 模块 import 期读一次 arq 的 Redis 配置。

    优先 arq_harness 段（写 yml_harness）；data_id 未配置（get_config_content
    抛 RuntimeError）时回落 arq_workflow 段（写 yml）——与 API 侧 bootstrap.py
    的 enable_arq_harness_redis 同一套回落：两侧都不建 arq_harness data_id 也
    指向同一 Redis，不会出现「任务投了没人消费」。

    读完即 close：单例被打回未初始化，bootstrap 里再 init 一次拿服务配置
    （幂等守卫会跳过已初始化的情况，不先关就再也读不到配置）。
    """
    await nacos_client.init(
        user_name=os.environ.get("nacos_name", Config.nacos_name),
        password=os.environ.get("nacos_password", Config.nacos_password),
        server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
        service_name=SERVICE_HARNESS,
        namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
        log_level=logging.NOTSET,
    )
    CustomRedisSettings.yml_harness = await nacos_client.get_config_content(ARQ_HARNESS)
    await nacos_client.close()


# bootstrap 幂等标记（同一进程内只初始化一次，防止异常重启路径重复初始化）
_BOOTSTRAPPED = False


async def bootstrap(ctx: dict) -> None:
    """harness worker 进程启动钩子（每 worker 进程执行一次）。

    业务后端全部从 Nacos 的 service_harness 段读（与 API 进程同源同段）：
    MySQL（会话/消息行）、业务 Redis（事件频道 publish）；opensandbox /
    agentscope 两段经 harness_settings 装载。arq 队列自己的 Redis 已在 import
    期由 prepare_config 填好（arq_harness 段，回落逻辑见其 docstring）。
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    # 0. 清理本 worker（hostname:pid）残留的旧健康 key（pid 复用场景兜底）
    from arq_tasks.worker_settings_harness import WorkerSettingsHarness
    try:
        await ctx["redis"].delete(worker_health_key(WorkerSettingsHarness.queue_name,
                                                    PIPELINE_HARNESS))
    except Exception as e:  # noqa: BLE001
        log.warning("clean stale harness worker health key failed: {}", e)

    # Windows 下使用 Selector 事件循环（与 uvicorn 多 worker 相同的策略）
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 1. 加载 service_harness 段（内含 mysql/redis/opensandbox/agentscope 各段）。
    # data_id 必须存在——mysql/redis 连接参数都在里面，读不到 worker 起不来
    #（部署前置：init_nacos.yaml 建 service_harness data_id，见 M3 部署配套）。
    await nacos_client.init(
        user_name=os.environ.get("nacos_name", Config.nacos_name),
        password=os.environ.get("nacos_password", Config.nacos_password),
        server_address=os.environ.get("nacos_server_address", Config.nacos_server_address),
        service_name=SERVICE_HARNESS,
        namespace_id=os.environ.get("nacos_namespace_id", Config.nacos_namespace_id),
        log_level=logging.NOTSET,
    )
    yml_config = await nacos_client.get_config_content(SERVICE_HARNESS)

    # 2. MySQL / 业务 Redis 连接池（与 service_harness API 进程同一套配置；
    #    事件频道的 publish 走业务 Redis，与 arq 队列连接是两回事）
    mysql_cfg = yml_config.get("mysql", {})
    await mysql_client.init(
        host=mysql_cfg.get("host"),
        port=int(mysql_cfg.get("port")),
        user=mysql_cfg.get("user"),
        password=quote_plus(mysql_cfg.get("password")),
        database=mysql_cfg.get("database"),
        pool_size=int(mysql_cfg.get("pool_size")),
        max_overflow=int(mysql_cfg.get("max_overflow")),
        echo=mysql_cfg.get("echo"),
    )
    redis_cfg = yml_config.get("redis", {})
    await redis.client.init(
        host=redis_cfg.get("host"),
        port=int(redis_cfg.get("port")),
        password=redis_cfg.get("password", ""),
        max_connections=redis_cfg.get("max_connections") or 200,
        db=int(redis_cfg.get("db") or 0),
    )

    # 3. opensandbox / agentscope 段装载（沙箱参数与 AgentState 存储两侧同源）
    from service.service_harness.services import harness_settings

    await harness_settings.bootstrap(yml_config)

    # 4. rag 检索后端（kb_search 桥的依赖）：ES / storage / Neo4j / rag_settings
    #    从 Nacos 的 arq_ragflow 段装载（ragflow worker 同款初始化；与 service_rag
    #    进程同源同段）。整段 try 降级：段缺失或连不上时 kb_search 工具报「后端
    #    不可用」（kb_search_tool.backend_ready 保持 False），worker 照常起——
    #    未挂知识库的轮次完全不受影响。mysql_client 幂等守卫会跳过已初始化的
    #    情况，rag 段的 mysql 配置不会覆盖 harness 段。
    try:
        from common.common_es import init_es
        from common.common_neo4j import init_neo4j
        from common.common_storage import init_storage

        harness_yml = await nacos_client.get_config_content(ARQ_HARNESS) or {}
        await init_storage(harness_yml.get("storage", {}))

        rag_yml = await nacos_client.get_config_content(ARQ_RAGFLOW) or {}
        await init_es(rag_yml.get("es"))
        await init_neo4j(rag_yml.get("neo4j"))
        from service.service_rag.services import rag_settings

        await rag_settings.bootstrap(rag_yml)
        from service.service_harness.agent.tools import kb_search_tool

        kb_search_tool.backend_ready = True
        log.info("harness kb_search backend ready (rag section loaded)")
    except Exception as e:  # noqa: BLE001
        log.warning("rag retrieval backend unavailable, kb_search degraded: {}", e)

    log.info("arq harness worker bootstrap done, queue={}",
             WorkerSettingsHarness.queue_name)
    _BOOTSTRAPPED = True


async def shutdown(ctx: dict) -> None:
    """harness worker 退出钩子：释放连接池（对未初始化单例是空操作）。"""
    try:
        from common.common_es import close_es

        await close_es()
    except Exception as e:  # noqa: BLE001
        log.warning("elasticsearch close failed: {}", e)
    try:
        from common.common_neo4j import close_neo4j

        await close_neo4j()
    except Exception as e:  # noqa: BLE001
        log.warning("neo4j close failed: {}", e)
    try:
        from common.common_storage import close_storage

        await close_storage()
    except Exception as e:  # noqa: BLE001
        log.warning("storage backend close failed: {}", e)
    await mysql_client.close()
    await redis.client.close()
    await nacos_client.close()
    log.info("arq harness worker shutdown done")


# ==================== 事件循环（累积 + 发布 + 中断检查） ====================

# 事件间隙的中断检查节流窗（秒）：每个 token 事件查一次 DB 会把 MySQL 打成热点，
# 2 秒一查的粒度足够（用户点中断到生效的最大延迟 = 窗口 + 一次事件间隔）
INTERRUPT_CHECK_WINDOW_S = 2.0


async def _publish(turn_id: str, event_type: str, payload: dict) -> None:
    """publish 的薄封装（worker 内统一出口，便于以后挂埋点）。"""
    await publish_turn_event(turn_id, event_type, payload)


# ==================== HITL 挂起恢复（demo drain_hitl 的跨轮版） ====================

# 授权确认挂起的「允许」判定词：英文按整词、中文按子串（BYPASS 权限模式下
# 不会出现授权挂起，这里为将来切 DEFAULT 模式留完整链路）
_CONFIRM_TOKENS = {"y", "yes", "ok", "allow", "confirm", "agree", "go", "continue"}
_CONFIRM_WORDS = ("是", "好", "对", "行", "允许", "同意", "确认", "可以", "继续", "执行")


def _is_confirm_reply(message: str) -> bool:
    """判断用户答复是否视为「允许执行」（授权确认挂起的意图解析）。"""
    lowered = (message or "").strip().lower()
    if not lowered:
        return False
    if set(lowered.split()) & _CONFIRM_TOKENS:
        return True
    return any(word in lowered for word in _CONFIRM_WORDS)


def _build_ask_user_metadata(tool_call, message: str) -> dict:
    """AskUser 回灌结果按 AskUserMetadata schema 组装 metadata。

    tool.check_external_result 会用 jsonschema 校验 metadata：顶层 required
    ["answers"]，每条 answer required ["question"]，可带 selected（标签数组）
    与 other（自由文本）。原来写死 metadata={} 会直接被挡下报
    "'answers' is a required property"。非 AskUser 的外部执行工具没有这条
    schema 约束，沿用空 metadata（维持原行为）。

    答案来源二选一：
    1. message 是 {"answers": [...]} 的 JSON（前端结构化答题口），直接采用；
    2. 否则把整条消息作为每个待答问题的 other 兜底，兼容纯文本回复。
    """
    import json

    from agentscope.tool import AskUser

    if tool_call.name != AskUser.name:
        return {}

    questions: list = []
    try:
        questions = json.loads(tool_call.input or "{}").get("questions") or []
    except (ValueError, TypeError, AttributeError):
        questions = []

    structured = None
    try:
        parsed = json.loads(message) if message else None
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("answers"), list):
        structured = parsed["answers"]

    answers: list = []
    if structured is not None:
        for item in structured:
            if not isinstance(item, dict):
                continue
            selected = item.get("selected")
            if not isinstance(selected, list):
                selected = [str(selected)] if selected not in (None, "") else []
            other = item.get("other")
            answers.append({
                "question": str(item.get("question") or ""),
                "selected": [str(s) for s in selected],
                "other": other if isinstance(other, str) and other else None,
            })
    else:
        for q in questions:
            q_text = str((q or {}).get("question") or "")
            if not q_text:
                continue
            answers.append({
                "question": q_text, "selected": [], "other": message or None,
            })

    if not answers:
        # 一个问题都没解析出来：给一条兜底，既满足 required 又不丢用户文本
        answers = [{"question": _awaiting_question([tool_call]),
                    "selected": [], "other": message or None}]
    return {"answers": answers}


def _render_answers(answers):
    """把 AskUser 的结构化答案渲染成模型可读文本（每题一行：问题：选项 / 补充）。

    output 是 ToolResultBlock 里给模型读的那一半，不能把 answers 的 JSON 原文
    直接喂给模型；非结构化（纯文本）回复时这里返回空串，调用方回落到原消息。
    """
    lines = []
    for a in answers or []:
        picked = "、".join(a.get("selected") or [])
        other = str(a.get("other") or "")
        val = " / ".join(x for x in (picked, other) if x)
        lines.append(f"{a.get('question', '')}：{val or '（未作答）'}")
    return "\n".join(lines)


def _hitl_resume_inputs(agent, message: str):
    """上轮挂起的 HITL 待答 → 本轮用户消息转成的恢复事件（无挂起返回 None）。

    demo 的 drain_hitl 在同一进程里 input() 逐个交互；worker 场景用户的答
    复由下一轮 POST /chat 带来——一条消息处理第一个挂起：SUBMITTED 态
    （AskUser / 外部执行）把消息整体回灌为执行结果，待授权态按消息意图
    给确认结果。答完由 reply_stream 继续驱动；若又挂起（还有多个待答）
    则本轮再次挂起，下轮继续收敛。
    """
    from agentscope.event import (
        ConfirmResult, ExternalExecutionResultEvent, UserConfirmResultEvent,
    )
    from agentscope.message import ToolCallState, ToolResultBlock, ToolResultState

    awaiting = agent.state.get_awaiting_tool_calls(agent.name)
    if not awaiting:
        return None
    tool_call = awaiting[0]
    if getattr(tool_call, "state", None) == ToolCallState.SUBMITTED:
        # AskUser / 外部工具在等执行结果：用户本轮消息整体即答案。
        # metadata 走 AskUserMetadata schema，output 是给模型读的人类文本：
        # 结构化答题时渲染成「问题：选项 / 补充」，别把 answers JSON 原文喂模型。
        metadata = _build_ask_user_metadata(tool_call, message)
        readable = _render_answers(metadata.get("answers"))
        return ExternalExecutionResultEvent(
            reply_id=agent.state.reply_id,
            execution_results=[ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=readable or message or "（用户未作答）",
                state=ToolResultState.SUCCESS,
                metadata=metadata,
            )],
        )
    return UserConfirmResultEvent(
        reply_id=agent.state.reply_id,
        confirm_results=[ConfirmResult(
            confirmed=_is_confirm_reply(message),
            tool_call=tool_call,
            rules=None,
        )],
    )


def _awaiting_question(awaiting: list) -> str:
    """把挂起的待答调用还原成问题文本（挂起提示与前端 question 字段共用）。

    AskUser 的入参是 {questions: [{question, options, ...}]} JSON 字符串；
    非 AskUser 挂起或入参不合法时回退到「工具名 + 入参截断」占位。
    """
    import json

    from agentscope.tool import AskUser

    tool_call = awaiting[0]
    raw = getattr(tool_call, "input", None) or "{}"
    if tool_call.name == AskUser.name:
        try:
            questions = json.loads(raw).get("questions") or []
            lines = [str(q.get("question") or "") for q in questions]
            lines = [line for line in lines if line]
            if lines:
                return "\n".join(lines)
        except (ValueError, TypeError, AttributeError):
            pass
    snippet = raw.strip()[:200]
    return f"{tool_call.name}({snippet})" if snippet else tool_call.name


def _pending_questions(awaiting: list) -> list[dict]:
    """AskUser 挂起 → 结构化待答问题（供前端渲染可填写选项列表）。

    AskUser 入参是 {questions:[{question, options:[{label,description}],
    multiSelect}]} JSON；每项折成 {question, options:[{label,description}],
    multiSelect} 带出。非 AskUser 挂起（授权确认）或入参不合法时返回
    []（前端退回纯文本回复）。
    """
    import json

    from agentscope.tool import AskUser

    tool_call = awaiting[0]
    if tool_call.name != AskUser.name:
        return []
    raw = getattr(tool_call, "input", None) or "{}"
    try:
        questions = json.loads(raw).get("questions") or []
    except (ValueError, TypeError, AttributeError):
        return []
    out: list[dict] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        q_text = str(q.get("question") or "")
        if not q_text:
            continue
        opts: list[dict] = []
        for o in (q.get("options") or []):
            if not isinstance(o, dict):
                continue
            label = str(o.get("label") or "")
            if not label:
                continue
            opts.append({"label": label,
                         "description": str(o.get("description") or "")})
        # multiSelect 兼容大小写两种写法（模型传 multiSelect、AskUserParams 存 multiselect）
        multi = q.get("multiSelect")
        if multi is None:
            multi = q.get("multiselect")
        out.append({"question": q_text, "options": opts,
                    "multiSelect": bool(multi)})
    return out


async def _stream_reply(turn_id: str, login_user: dict, snapshot: dict,
                        model, workspace, mounted) -> dict:
    """恢复 state → 组装 Agent → 跑一轮 reply_stream，返回累积结果。

    返回 dict：{status, content, thinking, tool_calls, error}——终态判定在
    _status_of；state 持久化在 finally（无论成败都要把记忆窗口写回 Redis，
    否则失败一轮就把之前的对话记忆丢了）。
    """
    # 事件转换与累积都在 worker 完成（harness_pubsub 保持纯通道，不 import
    # agentscope——API 进程不装 agentscope 也能起服务）
    from agentscope.event import (
        TextBlockDeltaEvent, ThinkingBlockDeltaEvent,
        ToolCallDeltaEvent, ToolCallEndEvent, ToolCallStartEvent,
    )
    from agentscope.message import UserMsg
    from agentscope.state import AgentState
    from agentscope.app.storage import RedisStorage, SessionConfig

    from service.service_harness.agent.harness_agent import AGENT_ID, build_agent
    from service.service_harness.agent.workspace_factory import workspace_id_of
    from service.service_harness.services.harness_settings import storage_options

    user_id = int(snapshot["userId"])
    session_id = int(snapshot["sessionId"])
    # RedisStorage 三级键（user/agent/session）一律字符串化：平台 id 是 int，
    # agentscope 的键拼接按 str 处理，两轮之间类型不一致会拆成两个会话
    user_key, session_key = str(user_id), str(session_id)
    # 会话绑定的 workspace_id 用 manager 分配的那个（workspace_scope 内 assign_workspace_id
    # 产出、get_workspace 回填到 workspace 对象），不再是旧的 harness-{user_id} 手工约定；
    # 取不到才回落到 workspace_id_of 兜底（不应发生，仅避免写空 workspace_id）。
    ws_id = str(getattr(workspace, "workspace_id", "") or "") or workspace_id_of(user_id)

    storage_opts = storage_options()
    if not storage_opts["password"]:
        # redis-py 对空串会发 AUTH（无密码的 Redis 会报错），None 才是不鉴权
        storage_opts["password"] = None

    # 累积器（终态落库的素材）
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    tool_calls_trace: list[dict] = []
    # tool_call_id -> (工具名, 入参片段)；End 事件只有 id，入参全靠 Delta 累积
    calls: dict[str, tuple[str, list[str]]] = {}
    interrupted = False

    async with RedisStorage(**storage_opts) as storage:
        record = await storage.get_session(user_id=user_key, agent_id=AGENT_ID,
                                           session_id=session_key)
        state = record.state if record else AgentState()
        agent = await build_agent(state, workspace, model,
                                  mounted_tools=mounted.tools,
                                  mount_summary=mounted.summary)
        loop = asyncio.get_running_loop()
        next_check = loop.time() + INTERRUPT_CHECK_WINDOW_S
        try:
            await _publish(turn_id, "turn.started", {})
            # M3 HITL 恢复：上轮挂起的待答（AskUser / 授权确认）由本轮用户
            # 消息续答（demo drain_hitl 的跨轮版，一轮答一个挂起）；无挂起才
            # 走正常 UserMsg
            inputs = _hitl_resume_inputs(agent, snapshot["message"])
            if inputs is None:
                inputs = UserMsg(name="user", content=snapshot["message"])
            async for evt in agent.reply_stream(inputs):
                if isinstance(evt, TextBlockDeltaEvent):
                    text_chunks.append(evt.delta)
                    await _publish(turn_id, EVT_TEXT_DELTA, {"content": evt.delta})
                elif isinstance(evt, ThinkingBlockDeltaEvent):
                    thinking_chunks.append(evt.delta)
                    await _publish(turn_id, EVT_THINKING_DELTA, {"content": evt.delta})
                elif isinstance(evt, ToolCallStartEvent):
                    calls[evt.tool_call_id] = (evt.tool_call_name, [])
                    await _publish(turn_id, EVT_TOOL_STARTED,
                                   {"tool": evt.tool_call_name,
                                    "toolCallId": evt.tool_call_id})
                elif isinstance(evt, ToolCallDeltaEvent):
                    calls.setdefault(evt.tool_call_id, ("", []))[1].append(evt.delta)
                elif isinstance(evt, ToolCallEndEvent):
                    name, arg_chunks = calls.pop(evt.tool_call_id, ("", []))
                    arguments = "".join(arg_chunks)
                    tool_calls_trace.append({
                        "tool": name, "toolCallId": evt.tool_call_id,
                        "arguments": arguments})
                    await _publish(turn_id, EVT_TOOL_COMPLETED,
                                   {"tool": name, "toolCallId": evt.tool_call_id,
                                    "arguments": arguments})
                # ReplyEndEvent 不发帧（终态帧唯一出口见模块头说明）；
                # ToolResult* / *Start 事件 M1 也不发帧，落库素材不收

                # 中断检查（时间窗节流）：DB 已被 interrupt 端点置 INTERRUPTED
                # 即停止消费后续事件，本轮按用户中断收尾。
                # 注：break 会 async-aclose reply_stream 生成器，state 里可能留下
                # 未闭合的工具调用——break 后用 UserInterruptEvent 闭合（见下）
                if loop.time() >= next_check:
                    next_check = loop.time() + INTERRUPT_CHECK_WINDOW_S
                    status = await HarnessSessionService.turn_status(turn_id)
                    if status is not None and status != TURN_RUNNING:
                        interrupted = True
                        break
            if interrupted:
                # 用户已明确要停：用 UserInterruptEvent 闭合 state 里未完成的
                # 工具调用（demo 的 InterruptReply 分支同款），闭合过程不再发
                # 帧；state 在 finally 照常写回。闭合失败只降级为未闭合，下轮
                # _hitl_resume_inputs 会遇到挂起、由用户消息续答兜底。
                try:
                    from agentscope.event import UserInterruptEvent
                    async for _ in agent.reply_stream(
                            UserInterruptEvent(reply_id=agent.state.reply_id)):
                        pass
                except Exception as e:  # noqa: BLE001
                    log.warning("harness interrupt close failed: turn={} {}",
                                turn_id, e)
        finally:
            # state 永远写回：记忆窗口 / HITL 待答上下文 / 任务清单都在里面，
            # 失败一轮不能把之前的对话记忆丢掉（与 demo 主循环同款 finally）
            await storage.upsert_session(
                user_id=user_key, agent_id=AGENT_ID,
                config=SessionConfig(workspace_id=ws_id),
                session_id=session_key, state=agent.state)

    # 终态判定顺序：用户中断 > HITL 挂起 > 完成
    if interrupted:
        return {"status": TURN_INTERRUPTED,
                "content": "".join(text_chunks) or None,
                "thinking": "".join(thinking_chunks) or None,
                "tool_calls": tool_calls_trace or None,
                "error": "用户中断了本轮回复"}
    awaiting = agent.state.get_awaiting_tool_calls(agent.name)
    if awaiting:
        # AskUser / 授权确认在等答复：state 已带待答上下文进 Redis，下一轮
        # 用户消息经 _hitl_resume_inputs 续答；问题文本随帧与落库行带出，
        # 前端挂起提示优先显示 question（计划帧协议 turn.interrupted 的口径）
        question = _awaiting_question(awaiting)
        pending = _pending_questions(awaiting)
        error = "智能体正在等待你的答复"
        if question:
            error = f"{error}：{question}"
        return {"status": TURN_INTERRUPTED,
                "content": "".join(text_chunks) or None,
                "thinking": "".join(thinking_chunks) or None,
                "tool_calls": tool_calls_trace or None,
                "error": error,
                "question": question,
                "pending": pending}
    return {"status": TURN_COMPLETED,
            "content": "".join(text_chunks) or None,
            "thinking": "".join(thinking_chunks) or None,
            "tool_calls": tool_calls_trace or None,
            "error": None}


async def run_harness_turn(ctx: dict, turn_id: str, login_user: dict) -> None:
    """执行一轮深度探索对话（POST /chat 投递的唯一任务入口）。

    turn 行已在 API 进程创建（ASSISTANT 行 RUNNING），这里恢复运行时、组装
    本轮挂载的平台能力、驱动 reply_stream、终态回写；login_user 是投递时
    带的 JWT 载荷 dict（无 token 明文），worker 侧凭它做挂载
    ACL（mount_loader 按 use 口径与 picker 同源校验）；模型已按 model_id 在可选
    清单接口收口授权，本轮不再回查。

    沙箱 workspace 用 workspace_scope（async with）包住整轮：进入起临时 manager、
    attach+initialize，整轮结束（含异常）由 __aexit__ 自动 close_all 释放，无需
    手工 teardown/逐出。
    """
    snapshot = await HarnessSessionService.turn_snapshot(turn_id)
    if snapshot is None or snapshot["status"] != TURN_RUNNING:
        # 幂等投递残留 / 用户中断先于消费：行上不是 RUNNING 就不跑
        log.warning("harness turn skipped: turn={} status={}",
                    turn_id, snapshot["status"] if snapshot else "missing")
        return

    try:
        from service.service_harness.agent.model_factory import build_model
        from service.service_harness.agent.tools.mount_loader import load_mounted
        from service.service_harness.agent.workspace_factory import workspace_scope

        model, _model_name = await build_model(int(snapshot["modelId"] or 0))
        # 挂载组装（三桥 + 技能种入）在 workspace 之前：技能解压目录要作为
        # skill_paths 传给 workspace（种入沙箱在 initialize 期完成）
        mounted = await load_mounted(login_user, snapshot.get("mounts") or {})
        async with workspace_scope(login_user, snapshot["sessionId"],
                                   skill_paths=mounted.skill_paths) as workspace:
            result = await _stream_reply(turn_id, login_user, snapshot, model,
                                         workspace, mounted)
            await HarnessSessionService.finish_turn(
                turn_id, result["status"], content=result["content"],
                thinking=result["thinking"], tool_calls=result["tool_calls"],
                error=result["error"], model_name=_model_name)
            # 终态帧（订阅方可能错过了中途的实时帧，这一帧保证关流信号必达）
            if result["status"] == TURN_COMPLETED:
                await _publish(turn_id, EVT_TURN_COMPLETED,
                               {"content": result["content"] or ""})
            elif result["status"] == TURN_INTERRUPTED:
                payload = {"reason": result["error"] or "本轮已中断"}
                if result.get("question"):
                    # HITL 挂起：问题文本单列一域，前端提示优先显示它
                    payload["question"] = result["question"]
                if result.get("pending"):
                    # AskUser 结构化待答问题：前端据此渲染可填写选项表单
                    payload["pendingQuestions"] = result["pending"]
                await _publish(turn_id, EVT_TURN_INTERRUPTED, payload)
    except BaseException as e:  # noqa: BLE001  兼 CancelledError：取消也算没跑完
        reason = f"{type(e).__name__}: {e}"[:1000]
        log.error("harness turn failed: turn={} {}", turn_id, reason)
        await HarnessSessionService.finish_turn(turn_id, TURN_FAILED, error=reason)
        try:
            await _publish(turn_id, EVT_TURN_FAILED, {"error": reason})
        except Exception:  # noqa: BLE001  回写失败不能再掩盖原始异常
            pass
        raise
