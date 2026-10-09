import asyncio
import html
import json
import os
import re
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from agentscope.workspace import LocalWorkspace
from pydantic import BaseModel, Field, SecretStr, ValidationError

from agentscope.agent import Agent
from agentscope.app.storage import RedisStorage, SessionConfig
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    ReplyFinishedReason,
    TextBlockDeltaEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import DashScopeChatModel
from agentscope.permission import (
    AdditionalWorkingDirectory,
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionMode,
)
from agentscope.state import AgentState
from agentscope.tool import (FunctionTool,
    AskUser,
    AskUserAnswer,
    AskUserMetadata,
    AskUserParams,
    Bash,
    Edit,
    Glob,
    Grep,
    PowerShell,
    Read,
    TaskCreate,
    TaskGet,
    TaskList,
    TaskUpdate,
    ToolBase,
    ToolChunk,
    Toolkit,
    Write,
)
FunctionTool()

USER_ID = "user_123"
AGENT_ID = "agent_456"
SESSION_ID = "session_789"
AGENT_NAME = "my_agent"

EXIT_KEYS = {"exit", "quit", "/exit"}
INTERRUPT_KEYS = {"i", "interrupt", "中断"}

# 权限模式。DONT_ASK 不再弹授权，凡是要问的一律直接拒绝（只读调用
# 和工作目录内的文件改动除外）；改回 PermissionMode.DEFAULT 则恢复逐次询问
PERMISSION_MODE = PermissionMode.BYPASS
# 工作目录，本脚本所在目录（test）
WORKING_DIRECTORIES = ["E:\\DRAGON-AI\\test"]

# reply / reply_stream 可接受的入参类型
ReplyInputs = (
    Msg
    | UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
    | None
)


class InterruptReply(Exception):
    """用户在命令行选择放弃本次挂起的回复。"""


def apply_permission_policy(state: AgentState) -> None:
    """把权限模式和工作目录写进 state，新会话与从 Redis 恢复的会话走同一入口。

    不变量：Agent 的权限引擎直接引用 state.permission_context 这个对象，
    因此必须在构造 Agent 之前改它，否则改动不生效。
    """
    ctx = state.permission_context
    ctx.mode = PERMISSION_MODE
    for directory in WORKING_DIRECTORIES:
        real_path = os.path.realpath(os.path.expanduser(directory))
        ctx.working_directories[real_path] = AdditionalWorkingDirectory(
            path=real_path,
            source="session",
        )


SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
SEARCH_TIMEOUT_SECONDS = 10.0
# 无脚本版检索页的固定入口，带浏览器 UA 才会被当成普通访问
SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# 一条结果 = 标题链接 + 紧跟其后的摘要链接
SEARCH_ITEM = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>"
    r'.*?<a[^>]*class="result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
    re.S,
)


def strip_tags(raw: str) -> str:
    """把结果片段里的 HTML 标签去掉，还原成纯文本。"""
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def decode_search_url(href: str) -> str:
    """把 DuckDuckGo 的跳转地址还原成真实目标网址。"""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def parse_search_results(body: str, limit: int) -> list[dict]:
    """从检索页 HTML 取前 limit 条结果，跳过广告位。"""
    results: list[dict] = []
    for matched in SEARCH_ITEM.finditer(body):
        url = decode_search_url(matched.group("url"))
        if "duckduckgo.com/y.js" in url:
            continue
        results.append(
            {
                "title": strip_tags(matched.group("title")),
                "url": url,
                "snippet": strip_tags(matched.group("snippet")),
            },
        )
        if len(results) >= limit:
            break
    return results


class WebSearchParams(BaseModel):
    """WebSearch 的入参。"""

    query: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "检索关键词，中英文均可；建议 2 到 8 个词，具体一些更容易命中。"
        ),
    )

    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="最多返回几条结果，默认 5 条。",
    )


class WebSearch(ToolBase):
    """联网检索公开网页，返回标题、地址与摘要。"""

    name: str = "WebSearch"

    description: str = (
        "联网搜索公开网页，返回每条结果的标题、链接和摘要。用于获取实时信息"
        "或不确定的事实，例如版本发布时间、新闻、官方文档位置。摘要可能不够"
        "完整，需要正文时把链接交给用户或再自己抓取。"
    )

    input_schema: dict[str, Any] = WebSearchParams.model_json_schema()

    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """只读公开网页，不改本地任何东西，任何模式下都放行。"""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="WebSearch 只读取公开网页，始终允许。",
        )

    async def call(
        self,
        query: str,
        max_results: int = 5,
    ) -> ToolChunk:
        """请求检索页并解析结果，失败时以 ERROR 状态把原因回给模型。"""
        try:
            async with httpx.AsyncClient(
                timeout=SEARCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    SEARCH_ENDPOINT,
                    params={"q": query},
                    headers={"User-Agent": SEARCH_USER_AGENT},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolChunk(
                content=[
                    TextBlock(text=f"搜索请求失败：{type(exc).__name__}: {exc}"),
                ],
                state=ToolResultState.ERROR,
            )

        results = parse_search_results(response.text, max_results)
        if not results:
            return ToolChunk(
                content=[
                    TextBlock(text=f"“{query}”没有检索到结果，换个说法再试。"),
                ],
            )

        lines = [f"“{query}”检索到 {len(results)} 条结果："]
        for index, item in enumerate(results, start=1):
            lines.append(f"{index}. {item['title']}")
            lines.append(f"   {item['url']}")
            lines.append(f"   {item['snippet']}")
        return ToolChunk(
            content=[TextBlock(text="\n".join(lines))],
            metadata={"results": results},
        )


async def build_toolkit(workspace: LocalWorkspace) -> Toolkit:
    """按能力分组组装工具集。

    不变量：Read/Write/Edit 与 Task* 都是 is_state_injected 工具，必须
    经由 Agent 连同 live state 一起调用，不能脱离 AgentState 直接执行。
    """
    return Toolkit(
        [
            # 向用户提问（外部工具，总是回到命令行）
            AskUser(),
            # 文件读取与检索
            Read(),
            Glob(),
            Grep(),
            # 联网检索
            WebSearch(),
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
        ],
        skills_or_loaders=await workspace.list_skills(agent_id=AGENT_ID)
    )


async def build_agent(state: AgentState, workspace: LocalWorkspace) -> Agent:

    """用恢复出来的状态组装智能体，工具集与模型保持固定。"""
    apply_permission_policy(state)
    return Agent(
        name=AGENT_NAME,
        system_prompt="你是一个有帮助的助手，可以直接调用工具完成任务，不必先向用户确认。",
        model=DashScopeChatModel(
            credential=DashScopeCredential(
                api_key=SecretStr(""),
            ),
            model="qwen-max",
        ),
        state=state,
        toolkit=await build_toolkit(workspace),
        offloader=workspace,

    )


def pending_tool_calls(agent: Agent) -> list[ToolCallBlock]:
    """本轮回复中还在等外部响应的工具调用（等授权 or 等外部执行结果）。"""
    return agent.state.get_awaiting_tool_calls(agent.name)


def pretty_input(raw: str) -> str:
    """把工具调用的原始 JSON 入参格式化输出，非法 JSON 时原样返回。"""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return raw


def compact_input(raw: str) -> str:
    """把工具调用的原始 JSON 入参压成单行，便于随调用日志输出。"""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False)
    except (TypeError, ValueError):
        return raw


class Printer:
    """流式片段与提示语共用一个命令行，避免出现半行。"""

    def __init__(self) -> None:
        self._line_open = False

    def write(self, text: str) -> None:
        """原样写出流式片段，不补行首标签。"""
        sys.stdout.write(text)
        sys.stdout.flush()
        self._line_open = not text.endswith("\n")

    def line(self, text: str) -> None:
        """整行写出提示语，必要时先结束上一段未收尾的流式文本。"""
        self.end_line()
        print(text)

    def end_line(self) -> None:
        """关闭还开着的行，保证下一个输入提示从行首开始。"""
        if self._line_open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._line_open = False


async def stream_reply(
        printer: Printer,
        agent: Agent,
        inputs: ReplyInputs,
) -> None:
    """跑一轮回复并按事件流渲染正文、思考、工具调用与工具输出。

    不变量：挂起的工具调用不在此处交互，只靠事件呈现；结束后由
    drain_hitl 从 state 里取出来交给用户处理，保证跨进程恢复时行为一致。
    """
    # tool_call_id -> (工具名, 入参片段)
    calls: dict[str, tuple[str, list[str]]] = {}
    # 待输出的工具名，只有真正有文本输出时才打标签
    pending_result: str | None = None

    async for evt in agent.reply_stream(inputs):
        if isinstance(evt, TextBlockStartEvent):
            printer.write("[助手] ")
        elif isinstance(evt, TextBlockDeltaEvent):
            printer.write(evt.delta)
        elif isinstance(evt, ThinkingBlockStartEvent):
            printer.line("[思考]")
        elif isinstance(evt, ThinkingBlockDeltaEvent):
            printer.write(evt.delta)
        elif isinstance(evt, ToolCallStartEvent):
            pending_result = None
            calls[evt.tool_call_id] = (evt.tool_call_name, [])
        elif isinstance(evt, ToolCallDeltaEvent):
            calls.setdefault(evt.tool_call_id, ("", []))[1].append(evt.delta)
        elif isinstance(evt, ToolCallEndEvent):
            name, chunks = calls.pop(evt.tool_call_id, ("", []))
            printer.line(f"[调用] {name} {compact_input(''.join(chunks))}")
        elif isinstance(evt, ToolResultStartEvent):
            pending_result = evt.tool_call_name
        elif isinstance(evt, ToolResultTextDeltaEvent):
            if pending_result is not None:
                printer.line(f"[输出] {pending_result}")
                pending_result = None
            printer.write(evt.delta)
        elif isinstance(evt, ToolResultEndEvent):
            # 正常成功的输出已经随流打完，只单独提醒异常终态
            if evt.state != ToolResultState.SUCCESS:
                printer.line(f"[结果] {evt.state}")
            pending_result = None
        elif isinstance(evt, ReplyEndEvent):
            if evt.finished_reason != ReplyFinishedReason.COMPLETED:
                printer.line(f"[提示] 本次回复结束：{evt.finished_reason}")

    printer.end_line()


def build_confirm_event(
        agent: Agent,
        tool_call: ToolCallBlock,
) -> UserConfirmResultEvent:
    """就地向用户要一次授权，返回授权结果事件；选择中断时抛 InterruptReply。"""
    print(f"\n===== 需要授权：{tool_call.name} =====")
    print(pretty_input(tool_call.input))
    if tool_call.suggested_rules:
        rules = "; ".join(
            f"{rule.behavior} {rule.tool_name}"
            + (f"({rule.rule_content})" if rule.rule_content else "")
            for rule in tool_call.suggested_rules
        )
        print(f"建议规则：{rules}")

    answer = input(
        "允许执行吗？[y] 本次允许 / [a] 总是允许 / [n] 拒绝 / [i] 中断：",
    ).strip().lower()
    if answer in INTERRUPT_KEYS:
        raise InterruptReply

    confirmed = answer in ("y", "yes", "a", "always")
    keep_rules = answer in ("a", "always") and bool(tool_call.suggested_rules)
    return UserConfirmResultEvent(
        reply_id=agent.state.reply_id,
        confirm_results=[
            ConfirmResult(
                confirmed=confirmed,
                tool_call=tool_call,
                rules=tool_call.suggested_rules if keep_rules else None,
            ),
        ],
    )


def collect_answer(question) -> AskUserAnswer:
    """渲染一道选择题并收集答案，序号 0 或非法序号表示自由输入。"""
    print(f"\n===== 智能体在问你：{question.question} =====")
    if question.context:
        print(f"（背景）{question.context}")
    for index, option in enumerate(question.options, 1):
        print(f"  {index}. {option.label} —— {option.description}")
    print("  0. 其他：直接输入你自己的答案")

    tip = "多选可用逗号分隔序号" if question.multi_select else "输入一个序号"
    while True:
        raw = input(f"{tip}（i 中断）：").strip()
        if raw.lower() in INTERRUPT_KEYS:
            raise InterruptReply

        tokens = [token for token in re.split(r"[,，;；\s]+", raw) if token]
        if tokens and all(token.isdigit() for token in tokens):
            indexes = [int(token) for token in tokens]
            labels: list[str] = []
            for index in indexes:
                if 1 <= index <= len(question.options):
                    label = question.options[index - 1].label
                    if label not in labels:
                        labels.append(label)
                    if not question.multi_select:
                        break
            if not labels:
                # 只选了 0 或越界序号，需要自由输入
                other = input("请输入你的答案：").strip()
                if other:
                    return AskUserAnswer(
                        question=question.question,
                        other=other,
                    )
                continue
            return AskUserAnswer(question=question.question, selected=labels)

        if raw:
            return AskUserAnswer(question=question.question, other=raw)


def build_external_event(
        agent: Agent,
        tool_call: ToolCallBlock,
) -> ExternalExecutionResultEvent:
    """替智能体执行外部工具（当前只有 AskUser），把结果回灌成事件。"""
    if tool_call.name != AskUser.name:
        print(f"\n===== 需要外部执行：{tool_call.name} =====")
        print(pretty_input(tool_call.input))
        raw = input("请输入执行结果（i 中断）：").strip()
        if raw.lower() in INTERRUPT_KEYS:
            raise InterruptReply
        return make_external_event(
            agent,
            tool_call,
            output=raw,
            state=ToolResultState.SUCCESS,
            metadata={},
        )

    try:
        params = AskUserParams.model_validate_json(tool_call.input or "{}")
    except ValidationError as error:
        # 入参本身不合法，按工具约定把错误原样回给智能体
        return make_external_event(
            agent,
            tool_call,
            output=f"AskUser 入参不合法：{error.errors()[0]['msg']}",
            state=ToolResultState.ERROR,
            metadata=AskUserMetadata(answers=[]).model_dump(mode="json"),
        )

    answers = [collect_answer(question) for question in params.questions]
    lines = [
        f"{answer.question}\n{answer.other or ', '.join(answer.selected)}"
        for answer in answers
    ]
    return make_external_event(
        agent,
        tool_call,
        output="\n\n".join(lines),
        state=ToolResultState.SUCCESS,
        metadata=AskUserMetadata(answers=answers).model_dump(mode="json"),
    )


def make_external_event(
        agent: Agent,
        tool_call: ToolCallBlock,
        *,
        output: str,
        state: ToolResultState,
        metadata: dict,
) -> ExternalExecutionResultEvent:
    """按工具要求的结构组装外部执行结果事件。"""
    return ExternalExecutionResultEvent(
        reply_id=agent.state.reply_id,
        execution_results=[
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=output,
                state=state,
                metadata=metadata,
            ),
        ],
    )


async def drain_hitl(printer: Printer, agent: Agent) -> None:
    """逐个把挂起的工具调用交给用户处理，直到本轮回复不再需要外部输入。"""
    while True:
        waiting = pending_tool_calls(agent)
        if not waiting:
            return

        tool_call = waiting[0]
        if tool_call.state == ToolCallState.SUBMITTED:
            event = build_external_event(agent, tool_call)
        else:
            event = build_confirm_event(agent, tool_call)

        await stream_reply(printer, agent, event)


async def main():
    printer = Printer()
    print(
        f"权限模式：{PERMISSION_MODE.value}；"
        f"工作目录：{', '.join(WORKING_DIRECTORIES)}"
    )
    workspace = LocalWorkspace(workdir="E:\\DRAGON-AI\\test",
                               skill_paths=["E:\\DRAGON-AI\\test\\skills\\.seed\\smart-charts"])
    await workspace.initialize()
    async with RedisStorage(
            host="127.0.0.1",
            port=26739,
            db=0,
            password="Jzh@616294",
    ) as storage:
        while True:
            # 从存储中加载状态，若不存在则使用全新状态
            record = await storage.get_session(
                user_id=USER_ID,
                agent_id=AGENT_ID,
                session_id=SESSION_ID,
            )
            state = record.state if record else AgentState()

            # 使用恢复的状态创建智能体
            agent = await build_agent(state, workspace)

            try:
                # 上一轮可能停在等授权或等外部执行，必须先处理完
                await drain_hitl(printer, agent)

                question = input("进行提问！").strip()
                if not question:
                    continue
                if question.lower() in EXIT_KEYS:
                    break

                await stream_reply(
                    printer,
                    agent,
                    UserMsg(name="user", content=question),
                )
            except (EOFError, KeyboardInterrupt):
                break
            except InterruptReply:
                # 放弃本次挂起的回复，闭合所有未完成的工具调用
                await stream_reply(
                    printer,
                    agent,
                    UserInterruptEvent(reply_id=state.reply_id),
                )
                printer.line("[提示] 已中断本次回复，可以继续提问。")
            finally:
                # 将更新后的状态持久化回 Redis
                await storage.upsert_session(
                    user_id=USER_ID,
                    agent_id=AGENT_ID,
                    config=SessionConfig(workspace_id=USER_ID),
                    session_id=SESSION_ID,
                    state=agent.state,
                )


if __name__ == "__main__":
    asyncio.run(main())
