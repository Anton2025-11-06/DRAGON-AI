# -*- coding: utf-8 -*-
"""智能体组装：AgentState + 沙箱 + 模型 → agentscope Agent。

组装口径（全部来自 service_harness/agentscope_chat.py demo 的实测路径）：
- 权限策略必须**在构造 Agent 之前**写进 state.permission_context（Agent 的
  权限引擎直接引用这个对象，构造后再改不生效）；
- Read/Write/Edit 与 Task* 是 is_state_injected 工具，必须经 Agent 连同
  live state 调用——因此 Toolkit 与 Agent 一起每轮重建（state 从 Redis 恢复，
  重建成本只是对象装配）；
- M1 工具集只有内置沙箱工具（Bash/文件四件/任务四件/AskUser）；M2 起
  build_toolkit 追加挂载的平台能力（kb_search / MCP / 平台工具三桥，
  mount_loader.load_mounted 产出，挂载白名单内的才进）。

RedisStorage 的会话键（user/agent/session 三级）：user = 平台 user_id、
agent = AGENT_ID（固定 harness_agent）、session = 平台会话 id——跨轮记忆窗口、
HITL 待答上下文都在这一份 state 里。

本模块只被 arq worker import（同 model_factory 的口径）。
"""

from __future__ import annotations

from typing import Any

from agentscope.agent import Agent
from agentscope.permission import (
    AdditionalWorkingDirectory, PermissionMode,
)
from agentscope.state import AgentState
from agentscope.tool import (
    AskUser, Bash, Edit, Glob, Grep, PowerShell, Read, TaskCreate, TaskGet,
    TaskList, TaskUpdate, Toolkit, Write,
)

# RedisStorage 的 agent 级键（user 级 = 平台 user_id，session 级 = 平台会话 id）
AGENT_ID = "harness_agent"
# Agent.name（ToolCallBlock 挂靠的消息名；state.get_awaiting_tool_calls 按它取）
AGENT_NAME = "deep_explore"

# M1 基础提示词：角色 + 沙箱边界 + 工作方法。M2 挂载四类能力时按挂载动态追加
# 能力说明段（哪个知识库可检索、哪些 MCP 工具可用）。
BASE_SYSTEM_PROMPT = (
    "你是「深度探索」智能体，运行在用户专属的云沙箱里，可以自主调用工具完成"
    "分析、探索与创作任务。\n"
    "- 沙箱工作目录：{workdir}。所有文件读写与命令都在沙箱里执行，文件跨会话保留，"
    "产出文件给用户时给出沙箱内路径。\n"
    "- 复杂任务先用 TaskCreate 拆解计划，边执行边用 TaskUpdate 更新状态。\n"
    "- 需要用户补充信息时用 AskUser 提问，不要自行猜测关键前提。\n"
    "- 回答使用中文，结论先行；涉及数据与文件时给出可核对的来源。"
)


def apply_permission_policy(state: AgentState, workdir: str) -> None:
    """把权限模式和工作目录写进 state（新会话与从 Redis 恢复的会话走同一入口）。

    不变量（demo 实测）：必须在构造 Agent **之前**改 state.permission_context，
    否则改动不生效。
    模式取 BYPASS：平台侧的边界在更外层——沙箱本身就是隔离环境（每用户独立
    实例、网络与资源限额在 OpenSandbox 侧配置），不需要 agentscope 逐次弹授权。
    """
    ctx = state.permission_context
    ctx.mode = PermissionMode.BYPASS
    ctx.working_directories[workdir] = AdditionalWorkingDirectory(
        path=workdir, source="session",
    )


async def build_toolkit(workspace: Any, mounted_tools: list | None = None) -> Toolkit:
    """组装工具集：内置沙箱工具 + 本轮挂载的平台能力工具（三桥产出）。

    skills_or_loaders 接 workspace.list_skills：沙箱 skills/ 目录下已种入的
    技能（技能 zip 挂载路径，种入由 workspace_factory 的 skill_paths 完成）
    在这里自动可用。
    """
    return Toolkit(
        [
            # 向用户提问（外部工具，HITL：挂起等答复）
            AskUser(),
            # 文件读取与检索
            Read(),
            Glob(),
            Grep(),
            # 文件写入与就地编辑
            Write(),
            Edit(),
            # 命令执行
            Bash(),
            PowerShell(),
            # 多步任务的计划与跟踪
            TaskCreate(),
            TaskUpdate(),
            TaskList(),
            TaskGet(),
            # 本轮挂载的平台能力（kb_search / MCP / 平台工具；无挂载时为空）
            *(mounted_tools or []),
        ],
        skills_or_loaders=await workspace.list_skills(agent_id=AGENT_ID),
    )


def _mount_section(summary: dict | None) -> str:
    """挂载摘要 → 系统提示词追加段（无挂载返回空串）。

    让模型知道本轮有哪些平台能力：不知道就会放着 kb_search 不用、自己去编。
    """
    summary = summary or {}
    lines: list[str] = []
    if summary.get("kbNames"):
        kbs = "、".join(summary["kbNames"])
        lines.append(f"- 知识库：可用 kb_search 工具检索（挂载：{kbs}）。")
    if summary.get("mcpNames"):
        mcps = "、".join(summary["mcpNames"])
        lines.append(f"- MCP 工具：mcp_ 前缀的工具（连接：{mcps}）。")
    if summary.get("toolNames"):
        tools = "、".join(summary["toolNames"])
        lines.append(f"- 平台工具：tool_ 前缀的工具（{tools}）。")
    if summary.get("skillNames"):
        skills = "、".join(summary["skillNames"])
        lines.append(f"- 技能：{skills}（指引已种入沙箱，按 SKILL.md 的方法执行）。")
    if not lines:
        return ""
    return "\n\n【本轮挂载的平台能力】\n" + "\n".join(lines)


def _build_system_prompt_sync(workspace: Any, mount_summary: dict | None = None) -> str:
    """拼系统提示词的基础段 + 挂载能力说明（同步部分，不含沙箱自述）。"""
    prompt = BASE_SYSTEM_PROMPT.format(workdir=str(getattr(workspace, "workdir", "")))
    return prompt + _mount_section(mount_summary)


async def _build_system_prompt(workspace: Any, mount_summary: dict | None = None) -> str:
    """拼系统提示词：基础段 + 沙箱自述 + 挂载能力说明。

    注意：OpenSandboxWorkspace.get_instructions 是 async 方法，必须 await。
    沙箱自述取不到不影响对话（降级为基础提示词）。
    """
    prompt = _build_system_prompt_sync(workspace, mount_summary)
    instructions = getattr(workspace, "get_instructions", None)
    if callable(instructions):
        try:
            extra = await instructions()
            if extra:
                prompt = f"{prompt}\n\n{extra}"
        except Exception:  # noqa: BLE001
            # 沙箱自述是锦上添花，取不到不影响对话
            pass
    return prompt


async def build_agent(state: AgentState, workspace: Any, model: Any,
                      mounted_tools: list | None = None,
                      mount_summary: dict | None = None) -> Agent:
    """用恢复出来的 state 组装智能体（工具集与模型每轮按最新配置重建）。

    mounted_tools / mount_summary 来自 mount_loader.load_mounted（本轮挂载的
    平台能力；无挂载时缺省空，行为与 M1 一致）。
    """
    apply_permission_policy(state, str(getattr(workspace, "workdir", "")))
    return Agent(
        name=AGENT_NAME,
        system_prompt=await _build_system_prompt(workspace, mount_summary),
        model=model,
        state=state,
        toolkit=await build_toolkit(workspace, mounted_tools),
        offloader=workspace,
    )
