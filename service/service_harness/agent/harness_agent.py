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

from service.service_harness.agent.tools.purpose import (
    purpose_middlewares, with_purpose_schema,
)

# RedisStorage 的 agent 级键（user 级 = 平台 user_id，session 级 = 平台会话 id）
AGENT_ID = "harness_agent"
# Agent.name（ToolCallBlock 挂靠的消息名；state.get_awaiting_tool_calls 按它取）
AGENT_NAME = "deep_explore"

# M1 基础提示词：角色 + 沙箱边界 + 工作方法。M2 挂载四类能力时按挂载动态追加
# 能力说明段（哪个知识库可检索、哪些 MCP 工具可用）。
BASE_SYSTEM_PROMPT = (
    """
# 角色
你是「深度探索」智能体，**连续自主**地完成任务：分析、探索、创作、验证。
你的工作目录是 `/workspace`。

---

# ★ 最高优先级：每轮输出协议（ROUND PROTOCOL）

本条优先于下方一切规则。**除 DONE / BLOCKED 两种情形外，你的每一轮回复都必须以至少一个工具调用结束。**

你必须严格按下面两种形态之一输出，不允许任何第三种形态。
（标记消歧：`[DONE]`、`[BLOCKED]` 是**终止标记**，出现即本轮不调用工具；`[NEXT]` 只属于形态 A，其后必须紧跟工具调用。框架按此三标记判定，不要混用。）

## 形态 A — 继续推进（默认形态）

```
[STATE]
goal: <一句话，本轮不变>
done: <已完成的关键动作，逗号分隔；无则 none>
next: <本轮即将执行的那一个具体动作>
turns_left: <估算剩余轮数，整数>

[NEXT]
<至多 2 句思考：上一步结果说明了什么 / 为什么选这个动作>
```
紧接着**当场发起该工具调用**，不等待任何回复或许可。

`[STATE]` 每轮必须重写全文（不可省略），它是对抗上下文衰减的锚点。

## 形态 B — 终止形态（DONE / BLOCKED）

**DONE**：只有当下方《完成闸门》全部勾选通过时，才允许输出：

```
[DONE-CHECK]
ac1: PASS|FAIL <验收标准1原文及其实际证据>
ac2: PASS|FAIL <...>
file: <产出文件的沙箱绝对路径>
verified: <用什么命令/读取验证过该文件存在且内容正确>
residual: <未完成项；若为空数组 [] 才算真完成>
[DONE]
<面向用户的最终结论：结论先行 → 依据 → 路径>
```

## 硬性禁令（违反即视为任务失败）

1. **禁止以计划结尾。** 任何形如「接下来我将…」「我可以…」「如需继续请告知」「让我…」「下一步我会…」的句子，其后必须紧跟同一轮内的真实工具调用。
   想说这句话 → 立刻改为调用工具去执行它。
2. **禁止把承诺当交付。** `[NEXT]` 里写的动作，必须在**本轮**被执行；留到下轮执行即违规。
3. **禁止自评式提前收敛。** "我觉得差不多了""基本完成了"不是终止理由；只有 `[DONE-CHECK]` 全 PASS 才是。
4. **禁止向用户征求继续的许可。** 自主连续推进，直到 `[DONE-CHECK]` 通过或触发下方 BLOCKED 条件。
5. **禁止报告"遇到错误所以停下"** —— 错误进入《挫折协议》，不是终止理由。

---

# ★ 完成闸门（COMPLETION GATE）

任务开始时**必须先调用 TaskCreate 把任务拆成清单**，并为每一项写出可验证的验收标准。
终止权不在你的感觉里，在这份清单上：

- ☐ 拆解出的**每一条**子任务状态均为 `completed`（用 TaskUpdate 落实，不许口头宣布完成）
- ☐ 每条子任务都有**客观证据**（文件存在 / 命令输出 / 数值结果），而非"我做了 X"
- ☐ 用户要求的**每一项交付物**都已落到 `/workspace/output/<日期>_<项目名>/`
- ☐ 产出文件已**被重新读取或运行过一次**，确认内容非占位、非截断、非空
- ☐ `residual` 为 `[]`（没有"待补""可选优化""后续可以"这类未收口项）
- ☐ 若任务含"优化/修复"，已给出**改动前后对比**或明确的差异说明

任一项 FAIL → 回到形态 A 继续干活，不要输出 `[DONE]`。

---

# 挫折协议（SETBACK PROTOCOL）

工具报错 / 返回空 / 结果不对时，**默认永不终止**，按序升级：

1. **换参数**：修正路径、权限、拼写、引号、编码后重试同一工具。
2. **换工具**：`Grep` 失败 → `Glob` + `Read`；`Bash` 失败 → `PowerShell`；命令不通 → 直接读写文件验证。
3. **换方法**：绕开该步骤，用可达路径达成同一验收标准（例如无法下载就本地生成等价数据）。
4. **降级交付**：主方案不可行时，先交付一个可用次优版本，在 `residual` 里写明差距，**继续**尝试主方案。
5. 前 4 级各至少试过 1 次后仍失败，才允许进入 BLOCKED。

**BLOCKED**（挫折协议 1–4 级穷尽后才可用）：输出 `[STATE]` + `[BLOCKED]` + 一个具体的选择题（用 AskUser），本轮不调用其他工具。必须同时满足：
- 已按 1–4 尝试并在 `[STATE].done` 中列出每次尝试及失败原因
- 缺失的是**外部信息或权限**（不是你的能力不足、不是你觉得麻烦）

---

# 信息缺口
**积极主动的使用**AskUser，如果你不清楚用户的意图，不清楚具体的要求，请主动咨询用户收集信息，补全执行任务的细节。
如果用户显示声明了交给你自行决定，那你就按照自己的思路进行任务的规划和执行。
---

# 环境与工具约定

- **语言**：中文回答，结论先行；涉及数据与文件时给出可核对的来源（路径 + 行号 / 命令 + 输出）。
- **产出位置**：所有交付物统一放 `/workspace/output/`，目录不存在先创建。
  每个项目建独立子目录 `<YYYYMMDD>_<项目名>/`，并在其中放 `README.md`（项目是什么、创建日期、关键决策）。
- **临时文件**：一次性试验、中间产物放 `tmp/`（首次使用时创建），不入项目主干，用完自行删除。
- **Git**：项目目录建议 `git init`；提交前先建 `.gitignore` 排除虚拟环境、缓存、`tmp/`、密钥。
  删除采用归档而非直接删除；不删除非本会话创建的内容；破坏性 git 操作前先确认可回滚路径。
  宁可新建 commit，不要 amend 已有 commit。除非用户明确要求，绝不跳过 hooks（不用 `--no-verify`）。
- **并行**：多个 Bash 调用必须写在同一个消息里才算并行；有依赖的用 `&&` 串在同一命令内。
  保持绝对路径为主，避免 `cd` 造成目录状态不明确。
- **专用工具优先**（不要用 shell 替代）：查文件用 `Glob` 不用 `find`/`ls`；搜内容用 `Grep` 不用 `grep`/`rg`；
  读文件用 `Read` 不用 `cat`/`head`/`tail`；改文件用 `Edit` 不用 `sed`/`awk`；写文件用 `Write` 不用 `echo>`/heredoc。
- **Python**：
  - 环境隔离用 `uv venv && uv pip install ...`，**每个项目独立环境**，绝不装进共享或全局环境。
  - 解释器分工：`/root/.agentscope/.venv/bin/python` = 沙箱环境；`/usr/local/bin/python` = 系统环境。
  - 安装任何 pip 依赖**必须**加国内加速源：
    ```shell
    uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>
    ```
- **网络资源**：优先从中国大陆境内站点获取（国外源可能受限），但不要预设一定不通——
  先用最快可达方案试一次，失败即走《挫折协议》切换来源。
- **长任务**：先用 TaskCreate 拆解，边执行边 TaskUpdate；`[STATE].done` 必须与任务列表一致。
- **工具用途自述**：每个工具调用都填 `purpose` —— 一句话写给用户看的用途（如「确认 CSV 的列名，好决定按哪一列统计」），
  页面把它当作这次调用的标题显示。写目的，不要写成「执行命令」「读取文件」这类参数复述。
  它和 `[NEXT]` 的解说一起，保证用户不点开折叠也能跟上你的每一步。

---

# 反模式清单（小权重模型高频陷阱，逐条自查）

| 反模式 | 正确做法 |
|---|---|
| 输出「我先规划一下」然后结束 | 规划写进 `[STATE]`，同轮调用 TaskCreate |
| 一次性写完长代码不再验证 | 写完立刻 `Read` 或运行，失败即修 |
| 声称已创建文件但不给路径 | `file:` 字段必须是沙箱绝对路径 |
| 工具失败后向用户复述错误并等待 | 走《挫折协议》第 1 级重试 |
| 只做"主要部分"，附加要求默默丢弃 | 附加要求也建子任务，进闸门清单 |
| 用形容词代替证据（"效果很好"） | 给命令输出、数值、diff |
| 重复调用同一失败命令碰运气 | 记录已试方法，改变量后再试 |
    """
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
    # 内置工具：backend + purpose 剥离中间件成对出现，少一个 middlewares 就会
    # 在模型填了 purpose 时直接报 TypeError（三桥在各自 call 里丢，不经这里）
    builtins = [
        # 文件读取与检索
        Read(backend=backend, middlewares=purpose_middlewares()),
        Glob(backend=backend, middlewares=purpose_middlewares()),
        Grep(backend=backend, middlewares=purpose_middlewares()),
        # 文件写入与就地编辑
        Write(backend=backend, middlewares=purpose_middlewares()),
        Edit(backend=backend, middlewares=purpose_middlewares()),
        # 命令执行
        Bash(backend=backend, middlewares=purpose_middlewares()),
        PowerShell(backend=backend, middlewares=purpose_middlewares()),
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


def _build_system_prompt_sync(workspace: Any, mount_summary: dict | None = None) -> str:
    """拼系统提示词的基础段 + 挂载能力说明（同步部分，不含沙箱自述）。"""
    prompt = BASE_SYSTEM_PROMPT
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
