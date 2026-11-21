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

提示词不在本模块里：主干段与运行时注入文案见 harness_prompts，自主性闸门见
harness_middlewares。本模块只负责按本轮上下文把它们拼起来。

RedisStorage 的会话键（user/agent/session 三级）：user = 平台 user_id、
agent = AGENT_ID（固定 harness_agent）、session = 平台会话 id——跨轮记忆窗口、
HITL 待答上下文都在这一份 state 里。

本模块只被 arq worker import（同 model_factory 的口径）。
"""

from __future__ import annotations

from typing import Any

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.permission import (
    AdditionalWorkingDirectory, PermissionMode,
)
from agentscope.state import AgentState
from agentscope.tool import (
    AskUser, Bash, Edit, Glob, Grep, PowerShell, Read, TaskCreate, TaskGet,
    TaskList, TaskUpdate, Toolkit, Write,
)

from service.service_harness.agent.harness_middlewares import AutonomyGuard
from service.service_harness.agent.harness_prompts import (
    RUNTIME_STATE_TEMPLATE, render_base_prompt,
)
from service.service_harness.agent.tools.present_output import PresentOutput
from service.service_harness.agent.tools.purpose import (
    purpose_middlewares, with_purpose_schema,
)

# RedisStorage 的 agent 级键（user 级 = 平台 user_id，session 级 = 平台会话 id）
AGENT_ID = "harness_agent"
# Agent.name（ToolCallBlock 挂靠的消息名；state.get_awaiting_tool_calls 按它取）
AGENT_NAME = "deep_explore"
# 提前收敛的纠正提醒预算（次/轮）：用完仍坚持收尾就放行，见 AutonomyGuard
MAX_CONTINUE_NUDGES = 3


def _injection_config() -> InjectionConfig:
    """运行时状态注入配置（时间 / 任务清单 / 上下文用量 / 连续报错）。

    每轮随 Agent 新建，避免共享实例被并发改写。这几类信息原本在旧版提示词里靠
    模型自己每轮重写 [STATE] 来对抗衰减——那是让模型替运行时记账；改走 agentscope
    内置的 HintBlock 注入后，prompt 缓存不受影响，模型也拿不到编造的余地。
    """
    return InjectionConfig(
        timezone="Asia/Shanghai",
        # 半小时内的重复提问不必再注一次时间
        time_interval=0.5,
        # 同一调用连续失败 3 次即提示换路子（与《走不通的时候》分级对齐）
        tool_retries_limit=3,
        template=RUNTIME_STATE_TEMPLATE,
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

    每个工具都带一个 `purpose` 入参（给用户读的用途自述，前端当工具行标题）：
    内置工具靠 middlewares 在执行前剥掉它——它们的 call 签名固定，多一个形参
    就是 TypeError；三桥自己声明并丢弃，故此处只给内置工具补 schema 与中间件。
    口径见 tools/purpose.py。
    """
    # 内置执行/文件工具必须显式绑到沙箱 backend：裸构造时 agentscope 会回落
    # LocalBackend，Bash/Read 等就在 worker 进程（本 app 镜像）里执行——表现为
    # ls /workspace 报 No such file、却能看见 /app 源码（工具根本没进沙箱）。
    backend = workspace.get_backend()
    # 轮内连接自愈：_attach 已把重连回调挂在 backend 上（单智能体与编排流水线同源）。
    # 取到就给每个内置沙箱工具装一层 SandboxReconnect（放最外层，包住实际执行）：
    # 长任务轮内沙箱被服务端回收时，工具命中连接错误即重连一次再重试，而不必整轮崩。
    # 取不到（未走 _attach 的调用方）则退回原口径，只装 purpose 剥离中间件。
    reconnect = getattr(backend, "_harness_reconnect", None)
    if reconnect is not None:
        from service.service_harness.agent.tools.sandbox_reconnect import (
            SandboxReconnect,
        )

        def _middlewares() -> list:
            return [SandboxReconnect(backend, reconnect), *purpose_middlewares()]
    else:
        def _middlewares() -> list:
            return purpose_middlewares()
    # 内置工具：backend + purpose 剥离中间件成对出现，少一个 middlewares 就会
    # 在模型填了 purpose 时直接报 TypeError（三桥在各自 call 里丢，不经这里）
    builtins = [
        # 文件读取与检索
        Read(backend=backend, middlewares=_middlewares()),
        Glob(backend=backend, middlewares=_middlewares()),
        Grep(backend=backend, middlewares=_middlewares()),
        # 文件写入与就地编辑
        Write(backend=backend, middlewares=_middlewares()),
        Edit(backend=backend, middlewares=_middlewares()),
        # 命令执行
        Bash(backend=backend, middlewares=_middlewares()),
        PowerShell(backend=backend, middlewares=_middlewares()),
        # 多步任务的计划与跟踪
        TaskCreate(middlewares=purpose_middlewares()),
        TaskUpdate(middlewares=purpose_middlewares()),
        TaskList(middlewares=purpose_middlewares()),
        TaskGet(middlewares=purpose_middlewares()),
    ]
    return Toolkit(
        [
            # 向用户提问（外部工具，HITL：挂起等答复）。它的入参本身就是给用户
            # 看的问题，再叠一层 purpose 自述只会多一个空字段，故不补 schema
            AskUser(),
            # 收尾登记最终交付文件（纯声明、无副作用、自带 purpose 形参，
            # 不经内置工具那套剥离中间件）；worker 侧识别这次调用发 turn.artifacts 帧
            PresentOutput(),
            *(with_purpose_schema(tool) for tool in builtins),
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
    kb_list = summary.get("kbList")
    if kb_list:
        lines.append(
            "- 知识库：可用 kb_search 检索。按下列清单如实作答，不要编造库名/kb_id/"
            "描述；要把检索限定到某个库时，kb_search 的 kb_ids 就填括号里的 kb_id：")
        for kb in kb_list:
            desc = str(kb.get("description") or "").strip() or "（无描述）"
            lines.append(f"    · {kb.get('name')}（kb_id={kb.get('id')}）：{desc}")
    elif summary.get("kbNames"):
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


async def _build_system_prompt(workspace: Any, mount_summary: dict | None = None,
                               base_prompt: str | None = None) -> str:
    """拼系统提示词：基础段 + 挂载能力说明 + 沙箱自述。

    base_prompt 传入即替掉主干段（编排的单/团队/目标/SOP 各成员自带提示词），
    后两段照拼——它们描述的是本轮真实能力，与是谁在用。

    注意：OpenSandboxWorkspace.get_instructions 是 async 方法，必须 await；
    沙箱自述取不到不影响对话（降级为不带自述的提示词）。
    """
    if base_prompt is None:
        base_prompt = render_base_prompt(str(getattr(workspace, "workdir", "")))
    prompt = base_prompt + _mount_section(mount_summary)
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
                      mount_summary: dict | None = None,
                      *, system_prompt: str | None = None,
                      agent_name: str = AGENT_NAME,
                      max_iters: int = 200) -> Agent:
    """用恢复出来的 state 组装智能体（工具集与模型每轮按最新配置重建）。

    mounted_tools / mount_summary 来自 mount_loader.load_mounted（本轮挂载的
    平台能力；无挂载时缺省空，行为与 M1 一致）。

    智能体编排参数化（缺省即深度探索原口径，保证回归不变）：
    - system_prompt 传入即覆盖深度探索那份主干段；仍自动拼接挂载能力说明段与
      沙箱自述；
    - agent_name 传入即覆盖 Agent.name（编排里按成员区分各 Agent）；
    - max_iters 透传 ReActConfig（编排成员可给更小的自主轮数）。
    """
    apply_permission_policy(state, str(getattr(workspace, "workdir", "")))
    return Agent(
        name=agent_name,
        system_prompt=await _build_system_prompt(
            workspace, mount_summary, system_prompt,
        ),
        model=model,
        state=state,
        toolkit=await build_toolkit(workspace, mounted_tools),
        # 自主性闸门：清单未走完就想纯文本收尾时压掉结束事件、再推一轮
        middlewares=[AutonomyGuard(max_nudges=MAX_CONTINUE_NUDGES)],
        offloader=workspace,
        react_config=ReActConfig(max_iters=max_iters),
        injection_config=_injection_config(),
    )
