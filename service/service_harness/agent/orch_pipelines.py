# -*- coding: utf-8 -*-
"""智能体编排流水线运行时（团队/目标/SOP 三模式，agentscope pipeline 真实执行）。

worker（arq_tasks.tasks.harness_pipeline.run_pipeline_turn）在一轮里这样用它：
1. plan()：把 runtime_config 的拓扑快照拆成「参与者清单」——每人带 Redis state
   键（agent_id/session）、唯一 Agent.name、system_prompt、mounts、流水线接线；
2. 逐参与者 load_mounted 收集全部 skill_paths → attach_workspace 一次（团队/目标/
   SOP 全部成员共享该用户单一沙箱工作区，天然共用，计划 §5）；
3. build_pipeline_stream()：按 plan 恢复各 state（共用 state 的模式同组只恢复一份）、
   逐参与者 build_model + 参数化 build_agent，组装 TeamPipeline / GoalPipeline /
   SOPEngine，返回 (stream, primary, persist_specs)；
4. worker 消费 stream（与深度探索同一套事件→帧转换）+ 收尾把各 state 写回 Redis。

AgentState 键隔离与各模式语义（计划 §5，用户明确要求）：
- 团队：主 Agent 一份 state（orch_team_{cid}_leader / str(sid)），子 Agent 各按
  主session_id + ":" + index 独立一份（并发绝不能共用，一成员一份）；
- 目标：executor 与 verifier 共用一份 state（顺序执行语义下成立）；
- SOP：流程所有节点共用一份 state（同上）。
"""
from __future__ import annotations

from typing import Any, Optional

DEFAULT_MAX_ITERS = 200


def plan(mode: str, cfg: dict, session_id: int) -> dict:
    """把 runtime_config 拓扑快照拆成参与者清单 + 流水线接线（纯计算，无 IO）。

    返回 {participants:[...], pipeline:{...}, primary_id:str}；participants 每项：
      id / agent_id_key / session_key / state_group(可空) / agent_name /
      system_prompt / mounts / description / subject / max_attempts。
    """
    cid = int(cfg.get("config_id") or 0)
    sid = str(session_id)
    if mode == "team":
        return _plan_team(cid, sid, cfg)
    if mode == "goal":
        return _plan_goal(cid, sid, cfg)
    if mode == "sop":
        return _plan_sop(cid, sid, cfg)
    raise ValueError(f"未知的编排流水线模式: {mode}")


def _participant(*, id_: str, agent_id_key: str, session_key: str, agent_name: str,
                 snapshot: dict, state_group: Optional[str] = None,
                 description: str = "", subject: str = "", max_attempts: int = 3) -> dict:
    snapshot = snapshot or {}
    return {
        "id": id_, "agent_id_key": agent_id_key, "session_key": session_key,
        "state_group": state_group, "agent_name": agent_name,
        "system_prompt": snapshot.get("system_prompt"),
        "mounts": snapshot.get("mounts") or {},
        "description": description or snapshot.get("description") or "",
        "subject": subject, "max_attempts": max_attempts,
    }


def _plan_team(cid: int, sid: str, cfg: dict) -> dict:
    leader_snap = cfg.get("leader") or {}
    members = cfg.get("members") or []
    leader = _participant(id_="leader", agent_id_key=f"orch_team_{cid}_leader",
                          session_key=sid, agent_name=f"leader_{cid}",
                          snapshot=leader_snap)
    participants = [leader]
    member_ids = []
    for m in members:
        index = int(m.get("index") if m.get("index") is not None else len(member_ids))
        mid = f"member:{index}"
        member_ids.append(mid)
        participants.append(_participant(
            id_=mid, agent_id_key=f"orch_team_{cid}_member",
            # 子 Agent 各按 主session_id:index 独立一份 state（并发不能共用）
            session_key=f"{sid}:{index}", agent_name=f"member_{cid}_{index}",
            snapshot=m, description=m.get("description") or ""))
    return {"participants": participants, "primary_id": "leader",
            "pipeline": {"kind": "team", "leader_id": "leader", "member_ids": member_ids,
                         "reset_members": bool(cfg.get("reset_members", True))}}


def _plan_goal(cid: int, sid: str, cfg: dict) -> dict:
    # executor 与 verifier 共用一份 state（同 agent_id_key/session_key/state_group）
    executor = _participant(id_="executor", agent_id_key=f"orch_goal_{cid}",
                            session_key=sid, agent_name=f"executor_{cid}",
                            snapshot=cfg.get("executor") or {}, state_group="shared")
    verifier = _participant(id_="verifier", agent_id_key=f"orch_goal_{cid}",
                            session_key=sid, agent_name=f"verifier_{cid}",
                            snapshot=cfg.get("verifier") or {}, state_group="shared")
    return {"participants": [executor, verifier], "primary_id": "executor",
            "pipeline": {"kind": "goal", "executor_id": "executor", "verifier_id": "verifier",
                         "max_iters": int(cfg.get("max_iters") or 10),
                         "max_retries": int(cfg.get("max_retries") or 3)}}


def _plan_sop(cid: int, sid: str, cfg: dict) -> dict:
    # 流程所有节点共用一份 state：同 agent_id_key/session_key/state_group="shared"
    participants = []
    steps = []
    for i, step in enumerate(cfg.get("steps") or []):
        exec_id, ver_id = f"step{i}:exec", f"step{i}:ver"
        participants.append(_participant(
            id_=exec_id, agent_id_key=f"orch_sop_{cid}", session_key=sid,
            agent_name=f"sop{cid}_step{i}_exec", snapshot=step.get("executor") or {},
            state_group="shared", subject=step.get("subject") or f"step{i}",
            description=step.get("description") or "",
            max_attempts=int(step.get("max_attempts") or 3)))
        ver = None
        if step.get("verifier"):
            participants.append(_participant(
                id_=ver_id, agent_id_key=f"orch_sop_{cid}", session_key=sid,
                agent_name=f"sop{cid}_step{i}_ver", snapshot=step["verifier"],
                state_group="shared"))
            ver = ver_id
        steps.append({"subject": step.get("subject") or f"step{i}",
                      "description": step.get("description") or "",
                      "max_attempts": int(step.get("max_attempts") or 3),
                      "executor_id": exec_id, "verifier_id": ver})
    if not steps:
        raise ValueError("SOP 至少需要一个流程节点")
    return {"participants": participants, "primary_id": steps[0]["executor_id"],
            "pipeline": {"kind": "sop", "name": cfg.get("name") or "sop", "steps": steps}}


async def build_pipeline_stream(plan_result: dict, *, storage: Any, user_key: str,
                                workspace: Any, login_user: dict,
                                mounted_map: dict, inputs: Any) -> dict:
    """按 plan 恢复 state → 建各 Agent → 组装流水线，返回 {stream, primary, persist}。

    mounted_map：participant.id → load_mounted 产出的 MountBundle（run_pipeline_turn 侧
    预先加载，避免二次种技能）；inputs：本轮喂给流水线的消息（UserMsg）。
    """
    from agentscope.app.storage import RedisStorage  # noqa: F401  仅确保依赖可用
    from agentscope.pipeline import GoalPipeline, TeamMember, TeamPipeline
    from agentscope.sop import SOP, SOPEngine, SOPStep
    from agentscope.state import AgentState

    from service.service_harness.agent.harness_agent import build_agent
    from service.service_harness.agent.model_factory import build_model

    participants = plan_result["participants"]
    primary_id = plan_result["primary_id"]

    # 1) 按 state_group 恢复 state：共用一组只恢复一份，其余成员复用同一对象
    states: dict[str, AgentState] = {}
    for p in participants:
        gk = p["state_group"] or p["id"]
        if gk not in states:
            record = await storage.get_session(user_id=user_key, agent_id=p["agent_id_key"],
                                               session_id=p["session_key"])
            states[gk] = record.state if record and record.state else AgentState()

    # 2) 逐参与者建 Agent（模型/挂载按各自快照，state 取所属组的那一份）
    agents: dict[str, Any] = {}
    for p in participants:
        bundle = mounted_map.get(p["id"])
        model, _ = await build_model(int((p["mounts"] or {}).get("modelId") or 0))
        agents[p["id"]] = await build_agent(
            states[p["state_group"] or p["id"]], workspace, model,
            mounted_tools=bundle.tools if bundle else None,
            mount_summary=bundle.summary if bundle else None,
            system_prompt=p["system_prompt"], agent_name=p["agent_name"],
            max_iters=DEFAULT_MAX_ITERS)

    # 3) 组装流水线并生成事件流
    meta = plan_result["pipeline"]
    if meta["kind"] == "team":
        members = [TeamMember(agent=agents[mid],
                              description=_member_desc(participants, mid))
                   for mid in meta["member_ids"]]
        pipeline = TeamPipeline(leader=agents[meta["leader_id"]], members=members,
                                reset_members=meta["reset_members"])
    elif meta["kind"] == "goal":
        pipeline = GoalPipeline(executor=agents[meta["executor_id"]],
                                verifier=agents[meta["verifier_id"]],
                                max_iters=meta["max_iters"], max_retries=meta["max_retries"])
    else:  # sop
        steps = [SOPStep(subject=s["subject"], description=s["description"],
                         executor=agents[s["executor_id"]],
                         verifier=agents[s["verifier_id"]] if s["verifier_id"] else None,
                         max_attempts=s["max_attempts"]) for s in meta["steps"]]
        pipeline = SOPEngine(SOP(name=meta["name"], steps=steps))

    # 4) 收尾写回清单：按 state_group 去重（共用 state 只写一次）
    seen: set = set()
    persist: list[tuple] = []
    for p in participants:
        gk = p["state_group"] or p["id"]
        if gk in seen:
            continue
        seen.add(gk)
        persist.append((p["agent_id_key"], p["session_key"], states[gk]))
    return {"stream": pipeline.reply_stream(inputs), "primary": agents[primary_id],
            "agents": agents, "persist": persist, "pipeline": pipeline}


def _member_desc(participants: list, member_id: str) -> str:
    for p in participants:
        if p["id"] == member_id:
            return p.get("description") or p.get("agent_name") or member_id
    return member_id
