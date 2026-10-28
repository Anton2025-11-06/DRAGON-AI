# -*- coding: utf-8 -*-
"""工具「用途说明」：给每个工具补一个 purpose 入参，并在真正执行前剥掉。

为什么加这个字段：模型在一长串工具调用之间往往一个字都不说，用户看到的就是一
排 Bash/Read/Edit 的机械名字，得点开折叠才知道在干什么。purpose 让模型每次调用
都写一句「这步要做什么、结果拿去干什么」，前端把它当工具行的标题（对齐豆包
办公 / workbuddy 的观感）。

为什么必须剥掉：agentscope 的 Toolkit 是
``tool(**_json_loads_with_repair(tool_call.input, tool.input_schema))``——模型
多传的键不会被 schema 拦下（内置工具的 schema 没有 additionalProperties: false），
而是原样进 ``call`` 的签名。Bash 的签名是 ``call(command, description, timeout)``，
多一个 ``purpose`` 就是 TypeError，被 toolkit 兜成一条 ERROR 结果回给模型，
表现为「工具莫名失败」，开发期看不出根因。所以走官方扩展点
:class:`~agentscope.tool.ToolMiddlewareBase`：中间件在执行前摘掉 purpose。

为什么不写进 required：模型漏填时 jsonschema 校验会把这次调用整个打回
（"Input validation failed"），长任务里等于自己给自己加重试；缺了由前端按入参
派生标签兜住（见 ui-ai home/index.vue 的 toolLabel）。
"""
from __future__ import annotations

import copy
import json
from typing import Any, AsyncGenerator, Callable, Optional

from agentscope.tool import ToolBase, ToolMiddlewareBase

# 用途字段的键名：worker 取标签、schema 注入、中间件剥离共用这一个约定
PURPOSE_KEY = "purpose"

# 写给模型看的字段说明：约束成「给用户读的一句话」，否则模型会把它当成
# 参数复述（写成 "执行 ls 命令" 这种对用户毫无信息量的话）
PURPOSE_DESCRIPTION = (
    "一句话说明这次调用要做什么、结果用来干什么（这句话会直接展示给用户看）。"
    "中文、不超过 40 字、动词开头；写目的而不是复述参数。"
)

# 语义上已经是「用途」的既有字段：Bash / PowerShell 自带 description 参数，
# 且两个工具的 call 都把它当纯注解收下、执行逻辑里根本不用（见 _bash.py 的
# call 体内只读 command/timeout）。统一成 purpose 一条规则，模型少填一个同义
# 字段；老数据/模型仍写 description 时由 extract_purpose 的别名兜底读出标签。
_PURPOSE_ALIAS: dict[str, str] = {
    "Bash": "description",
    "PowerShell": "description",
}

# 注入 purpose 时一并从 schema 里撤掉的同义字段（不进 required 才敢撤）
_PURPOSE_REPLACES: dict[str, tuple[str, ...]] = {
    "Bash": ("description",),
    "PowerShell": ("description",),
}

# 展示用的截断长度：模型可能写一整段，工具行标题只放得下一句
PURPOSE_MAX_CHARS = 200


class PurposeStripper(ToolMiddlewareBase):
    """执行前摘掉 purpose——它只给用户看，工具本身不需要这个参数。"""

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        kwargs = {
            key: value
            for key, value in (input_kwargs or {}).items()
            if key != PURPOSE_KEY
        }
        async for chunk in next_handler(**kwargs):
            yield chunk


def purpose_middlewares() -> list[ToolMiddlewareBase]:
    """内置工具构造参数用的 middlewares 列表（每工具一份实例，不共享列表）。"""
    return [PurposeStripper()]


def with_purpose_schema(tool: ToolBase) -> ToolBase:
    """给工具的 input_schema 补 purpose（实例级副本，原地不动类属性）。

    必须 deepcopy：``input_schema`` 是类属性上的同一个 dict，原地加字段会污染
    该类的所有实例且跨轮累积，副本赋给实例后只有这一个工具看得到 purpose。

    已自带 purpose 的工具（我们的三桥在类里就声明了）走 setdefault，不覆盖
    各自的描述文案。
    """
    schema = copy.deepcopy(getattr(tool, "input_schema", None) or {})
    if not isinstance(schema, dict):
        return tool
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        schema["properties"] = properties
    for stale in _PURPOSE_REPLACES.get(str(getattr(tool, "name", "")), ()):
        # 只撤 schema 声明，不动工具签名：模型若仍按老习惯传 description，
        # 会落进工具本来就有默认值的形参，不会 TypeError
        properties.pop(stale, None)
    properties.setdefault(PURPOSE_KEY, {
        "type": "string",
        "description": PURPOSE_DESCRIPTION,
    })
    tool.input_schema = schema
    return tool


def extract_purpose(tool_name: str, arguments: Optional[str]) -> Optional[str]:
    """工具入参 JSON → 给用户读的用途文本（发帧、落库、回放三处共用一个口径）。

    取不到（入参不是合法 JSON、模型没填、只有空白）返回 None，由前端退回按
    入参派生的标签，而不是显示一个空的工具行。
    """
    if not arguments:
        return None
    try:
        parsed = json.loads(arguments)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    keys = [PURPOSE_KEY]
    alias = _PURPOSE_ALIAS.get(str(tool_name))
    if alias:
        keys.append(alias)
    for key in keys:
        text = str(parsed.get(key) or "").strip()
        if text:
            return text[:PURPOSE_MAX_CHARS]
    return None
