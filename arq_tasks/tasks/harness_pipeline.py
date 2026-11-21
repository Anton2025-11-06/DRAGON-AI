# -*- coding: utf-8 -*-
"""arq harness 编排流水线轮次（团队/目标/SOP）：run_pipeline_turn。

深度探索与单智能体走 harness.py 的 _stream_reply（单 Agent）；team/goal/sop 是多
Agent 流水线，state 恢复/持久化与事件消费都在这里，与 _stream_reply 用同一套
「事件→帧」转换口径（harness_pubsub 常量 + publish + 停止监视器），验证台前端无感。

一轮流程（run_harness_turn 判定 orch_mode∈{team,goal,sop} 后调进来）：
1. plan：orch_pipelines.plan 把 runtime_config 拓扑拆成参与者清单；
2. 逐参与者 load_mounted → 收集全部 skill_paths → attach_workspace 一次（沙箱共用）；
3. RedisStorage 里 build_pipeline_stream 恢复各 state、建各 Agent、组装流水线；
4. 消费 pipeline.reply_stream（_run_with_stop_watch 包取消）+ 收尾把各 state 写回；
5. 返回与 _stream_reply 同形状的结果 dict，交 run_harness_turn 统一 finish_turn + 终态帧。

已知边界（计划 §风险）：流水线跨轮 HITL 续答（AskUser/授权挂起后下一轮恢复）依赖
流水线实例态持久化，本实现按「每轮全新构建 + AgentState 记忆窗口连续」跑通单轮；
挂起检测覆盖全部参与者，中断收尾尽力闭合。团队模式子 Agent 各独立 state（并发）、
目标/SOP 共用 state（顺序）——语义见 orch_pipelines 模块头。
"""
from __future__ import annotations

from common.common_log.log_init import log

from service.service_harness.models.harness_entity import (
    TURN_COMPLETED, TURN_INTERRUPTED,
)
from service.service_harness.services.harness_pubsub import (
    EVT_TEXT_DELTA, EVT_THINKING_DELTA, EVT_TOOL_COMPLETED, EVT_TOOL_STARTED,
    EVT_TURN_ARTIFACTS,
)


async def run_pipeline_turn(turn_id: str, login_user: dict, snapshot: dict,
                            mode: str, cfg: dict) -> dict:
    """跑一轮多 Agent 流水线编排（team/goal/sop），返回终态结果 dict。"""
    from agentscope.app.storage import RedisStorage, SessionConfig
    from agentscope.event import (
        HintBlockEvent, TextBlockDeltaEvent, ThinkingBlockDeltaEvent,
        ToolCallDeltaEvent, ToolCallEndEvent, ToolCallStartEvent,
    )
    from agentscope.message import UserMsg
    from agentscope.state import AgentState

    from arq_tasks.tasks.harness import (
        _compose_user_message, _parse_present_output_args, _publish,
        _publish_nudge_frame, _run_with_stop_watch,
    )
    from service.service_harness.agent import orch_pipelines
    from service.service_harness.agent.tools.mount_loader import load_mounted
    from service.service_harness.agent.tools.purpose import extract_purpose
    from service.service_harness.agent.workspace_factory import (
        attach_workspace, workspace_id_of,
    )
    from service.service_harness.services.harness_settings import storage_options

    user_id = int(snapshot["userId"])
    session_id = int(snapshot["sessionId"])
    user_key = str(user_id)

    plan_result = orch_pipelines.plan(mode, cfg, session_id)
    participants = plan_result["participants"]

    # 1) 逐参与者装配挂载（三桥 + 技能种入），收集全部 skill_paths 供 workspace 一次种入
    mounted_map: dict[str, object] = {}
    skill_paths: list = []
    for p in participants:
        bundle = await load_mounted(login_user, p["mounts"] or {})
        mounted_map[p["id"]] = bundle
        skill_paths.extend(bundle.skill_paths or [])

    # 2) 沙箱工作区共用：一次 attach 带全部成员技能
    workspace = await attach_workspace(login_user, session_id, skill_paths=skill_paths)
    ws_id = str(getattr(workspace, "workspace_id", "") or "") or workspace_id_of(user_id)

    storage_opts = storage_options()
    if not storage_opts["password"]:
        storage_opts["password"] = None

    # 累积器（与 _stream_reply 同口径的落库素材）
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    tool_calls_trace: list[dict] = []
    calls: dict[str, tuple[str, list[str]]] = {}
    artifacts: list[dict] = []
    interrupted = False

    built: dict = {}
    async with RedisStorage(**storage_opts) as storage:
        inputs = UserMsg(name="user", content=_compose_user_message(
            snapshot["message"], snapshot.get("attachments")))
        built = await orch_pipelines.build_pipeline_stream(
            plan_result, storage=storage, user_key=user_key, workspace=workspace,
            login_user=login_user, mounted_map=mounted_map, inputs=inputs)
        primary = built["primary"]
        stream = built["stream"]
        try:
            await _publish(turn_id, "turn.started", {})

            async def _consume_events() -> None:
                async for evt in stream:
                    if isinstance(evt, TextBlockDeltaEvent):
                        text_chunks.append(evt.delta)
                        await _publish(turn_id, EVT_TEXT_DELTA, {"content": evt.delta})
                    elif isinstance(evt, ThinkingBlockDeltaEvent):
                        thinking_chunks.append(evt.delta)
                        await _publish(turn_id, EVT_THINKING_DELTA,
                                       {"content": evt.delta})
                    elif isinstance(evt, ToolCallStartEvent):
                        calls[evt.tool_call_id] = (evt.tool_call_name, [])
                        if evt.tool_call_name != "present_output":
                            await _publish(turn_id, EVT_TOOL_STARTED,
                                           {"tool": evt.tool_call_name,
                                            "toolCallId": evt.tool_call_id})
                    elif isinstance(evt, ToolCallDeltaEvent):
                        calls.setdefault(evt.tool_call_id, ("", []))[1].append(evt.delta)
                    elif isinstance(evt, ToolCallEndEvent):
                        name, arg_chunks = calls.pop(evt.tool_call_id, ("", []))
                        arguments = "".join(arg_chunks)
                        if name == "present_output":
                            known = {a["path"] for a in artifacts}
                            for item in _parse_present_output_args(arguments):
                                if item["path"] not in known:
                                    artifacts.append(item)
                                    known.add(item["path"])
                            if artifacts:
                                await _publish(turn_id, EVT_TURN_ARTIFACTS,
                                               {"artifacts": artifacts})
                            continue
                        purpose = extract_purpose(name, arguments)
                        trace = {"tool": name, "toolCallId": evt.tool_call_id,
                                 "arguments": arguments}
                        if purpose:
                            trace["purpose"] = purpose
                        tool_calls_trace.append(trace)
                        payload = {"tool": name, "toolCallId": evt.tool_call_id,
                                   "arguments": arguments}
                        if purpose:
                            payload["purpose"] = purpose
                        await _publish(turn_id, EVT_TOOL_COMPLETED, payload)
                    elif isinstance(evt, HintBlockEvent):
                        # 闸门纠正提醒 → turn.nudge（与 _stream_reply 同一处口径，
                        # 其余来源的 hint 不发帧）
                        await _publish_nudge_frame(turn_id, evt)
            interrupted = await _run_with_stop_watch(turn_id, _consume_events)
            if interrupted:
                # 用户点停止：以流水线为口发 UserInterruptEvent 闭合挂起工具调用（尽力）
                try:
                    from agentscope.event import UserInterruptEvent
                    async for _ in built["pipeline"].reply_stream(
                            UserInterruptEvent(reply_id=primary.state.reply_id)):
                        pass
                except Exception as e:  # noqa: BLE001
                    log.warning("orch pipeline interrupt close failed: turn={} {}",
                                turn_id, e)
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                try:
                    await stream.aclose()
                except Exception as e:  # noqa: BLE001
                    log.warning("orch pipeline reply_stream aclose failed: turn={} {}",
                                turn_id, e)
            # 各参与者 state 一律写回（共用组已在 persist 去重）
            for agent_id_key, session_key, state in built["persist"]:
                await storage.upsert_session(
                    user_id=user_key, agent_id=agent_id_key,
                    config=SessionConfig(workspace_id=ws_id),
                    session_id=session_key, state=state)

    # 终态判定：用户中断 > HITL 挂起（任一参与者有待答）> 完成
    if interrupted:
        return {"status": TURN_INTERRUPTED,
                "content": "".join(text_chunks) or None,
                "thinking": "".join(thinking_chunks) or None,
                "tool_calls": tool_calls_trace or None,
                "artifacts": artifacts or None,
                "error": "用户中断了本轮回复"}
    awaiting, awaiting_agent = _find_awaiting(built["agents"])
    if awaiting:
        from arq_tasks.tasks.harness import _awaiting_question, _pending_questions
        question = _awaiting_question(awaiting)
        error = f"{awaiting_agent.name} 正在等待你的答复"
        if question:
            error = f"{error}：{question}"
        return {"status": TURN_INTERRUPTED,
                "content": "".join(text_chunks) or None,
                "thinking": "".join(thinking_chunks) or None,
                "tool_calls": tool_calls_trace or None,
                "artifacts": artifacts or None,
                "error": error, "question": question,
                "pending": _pending_questions(awaiting)}
    return {"status": TURN_COMPLETED,
            "content": "".join(text_chunks) or None,
            "thinking": "".join(thinking_chunks) or None,
            "tool_calls": tool_calls_trace or None,
            "artifacts": artifacts or None,
            "error": None}


def _find_awaiting(agents: dict):
    """遍历全部参与者，取第一个有挂起待答的（team 队长停在派活、成员停在 AskUser）。"""
    for agent in agents.values():
        try:
            awaiting = agent.state.get_awaiting_tool_calls(agent.name)
        except Exception:  # noqa: BLE001
            awaiting = None
        if awaiting:
            return awaiting, agent
    return None, None
