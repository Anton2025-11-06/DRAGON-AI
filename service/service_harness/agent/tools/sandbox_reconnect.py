# -*- coding: utf-8 -*-
"""沙箱连接自愈中间件：长任务轮内命中沙箱连接错误时重连一次再重试。

为什么要在轮内补这一层（对应 arq 侧长任务报 Network connectivity error）：
- run_harness_turn 每轮只在**开头** attach 一次拿到 workspace/backend，之后整轮
  reply_stream（可达数十分钟、上百次工具调用）都复用这一个 backend 句柄；
- 探活 + 逐出重建只在 _attach 那一刻做一次，轮内不再校验。沙箱若在轮次中途被
  服务端按最大存活时间销毁 / 暂停，后续每次工具调用都打在死句柄上——表现为
  Bash / Write / Glob 全部返回 httpx.ConnectError「All connection attempts failed」，
  整轮剩余步骤全崩；
- 页面侧（API 进程）不受影响，是因为文件浏览**每次请求都重新 attach**，天然自愈。

本中间件把这层「自愈」补到 worker 轮内：包住内置沙箱工具的执行，命中连接类错误
时触发一次重连（重新 attach → 探活 + 逐出 + 重建），把新沙箱句柄**原地换进所有工具
共享的那个 backend**，再重试当前这次调用。只重试一次；重连失败则如实透传原结果 /
异常，绝不吞错。

连接失败有两种表现，这里都拦：
- Bash / PowerShell / Glob / Grep：OpenSandboxBackend.exec_shell 把传输层异常吞成
  exit_code=-1 再包成一条 state=ERROR 的结果 chunk（不抛异常），故要看 chunk 文本；
- Read / Write / Edit：backend.read_file / write_file 直接 raise（httpx.ConnectError），
  故要 catch 异常。

只作用于绑定了沙箱 backend 的内置执行 / 文件工具；AskUser / PresentOutput / 三桥等
平台工具不经这里（无 backend，也不会在轮内被沙箱回收波及）。

误判代价可控：若某条用户命令自己的输出里恰好含「connection refused」等字样，会被
误判为沙箱掉线，多跑一次重连 + 一次重试——但每个调用只重试一次，不会循环，且重连
幂等，最坏只是多一次往返，不影响正确性。
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Awaitable, Callable

from agentscope.tool import ToolBase, ToolMiddlewareBase

from common.common_log.log_init import log

# 传输层 / 沙箱不可达的特征串（小写匹配）。刻意不含 "connection refused" 这类
# 沙箱内部命令自己也可能打印的字样——那只沙箱本身是通的、不该触发重连。
_CONN_MARKERS: tuple[str, ...] = (
    "all connection attempts failed",
    "connecterror",
    "network connectivity error",
    "failed to establish a new connection",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "remoteprotocolerror",
    "server disconnected",
    "incompleteread",
    "readerror",
)

# 直接按异常类型名判连接错误（httpx 及 SDK 常见传输异常），比字符串匹配更准。
_CONN_EXC_NAMES: frozenset[str] = frozenset({
    "ConnectError", "ConnectTimeout", "NetworkError", "TransportError",
    "RemoteProtocolError", "ReadError", "WriteError", "PoolTimeout",
})


def _chunk_text(chunk: Any) -> str:
    """把 ToolChunk 的文本块拼成纯文本（非文本块跳过），供特征串匹配。"""
    parts: list[str] = []
    for block in getattr(chunk, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _is_conn_text(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _CONN_MARKERS)


def _is_conn_exception(exc: BaseException) -> bool:
    """异常是否为沙箱传输层错误：按类型名链 + httpx.TransportError + 文本兜底。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in _CONN_EXC_NAMES:
            return True
        if _is_conn_text(str(cur)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _chunk_is_conn_error(chunk: Any) -> bool:
    """结果 chunk 是否为「沙箱连接失败」：ERROR 态 + 文本命中传输特征。"""
    state = getattr(chunk, "state", None)
    name = str(getattr(state, "name", state) or "").upper()
    if "ERROR" not in name:
        return False
    return _is_conn_text(_chunk_text(chunk))


class SandboxReconnect(ToolMiddlewareBase):
    """包住内置沙箱工具：命中连接错误即重连一次、原地换回活句柄再重试。

    :param backend: 本轮所有内置工具共享的那个 OpenSandboxBackend 实例（重连后
        原地把它的 ``_sandbox`` / ``_workdir`` 换成新沙箱，即对全部工具生效）。
    :param reconnect: 无参 async 回调，重新 attach 一只确实在 running 的沙箱并把
        新句柄换进 ``backend``；返回 True 表示已换、可重试，False 表示没换成。
    """

    def __init__(self, backend: Any, reconnect: Callable[[], Awaitable[bool]]) -> None:
        self._backend = backend
        self._reconnect = reconnect

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        # 内置沙箱工具都是「单结果 chunk」，可安全整段缓冲后再判类型 / 决定重试，
        # 不会破坏流式语义（真正的文本流式在模型侧，不经这里）。
        chunks, exc = await self._drain(next_handler, input_kwargs)
        if not self._is_conn_failure(chunks, exc):
            for chunk in chunks:
                yield chunk
            if exc is not None:
                raise exc
            return

        # 命中连接错误：重连一次，成功则用新句柄重试当前调用，失败则透传原样。
        log.warning("harness 沙箱连接错误，轮内重连后重试: tool={}",
                    getattr(tool, "name", ""))
        reconnected = False
        try:
            reconnected = bool(await self._reconnect())
        except Exception as re:  # noqa: BLE001  重连本身失败不能再炸整轮
            log.warning("harness 沙箱轮内重连失败: tool={} {}",
                        getattr(tool, "name", ""), re)
        if not reconnected:
            for chunk in chunks:
                yield chunk
            if exc is not None:
                raise exc
            return

        chunks2, exc2 = await self._drain(next_handler, input_kwargs)
        for chunk in chunks2:
            yield chunk
        if exc2 is not None:
            raise exc2

    @staticmethod
    async def _drain(next_handler: Callable[..., AsyncGenerator[Any, None]],
                     input_kwargs: dict[str, Any]) -> tuple[list[Any], BaseException | None]:
        """把一次工具执行完整跑完，收集结果 chunk 与（若有）抛出的异常。"""
        chunks: list[Any] = []
        exc: BaseException | None = None
        try:
            async for chunk in next_handler(**input_kwargs):
                chunks.append(chunk)
        except Exception as e:  # noqa: BLE001  含 httpx.ConnectError：交调用方判定
            exc = e
        return chunks, exc

    @staticmethod
    def _is_conn_failure(chunks: list[Any], exc: BaseException | None) -> bool:
        if exc is not None:
            return _is_conn_exception(exc)
        return any(_chunk_is_conn_error(chunk) for chunk in chunks)
