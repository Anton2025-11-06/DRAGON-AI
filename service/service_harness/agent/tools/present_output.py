# -*- coding: utf-8 -*-
"""present_output 桥：让智能体在收尾时显式「登记最终交付文件」。

为什么用「显式声明」而不是前端解析回复正文 / 扫工具结果：
- 深度探索是 ReAct 智能体，一轮里 Write/Edit/Bash 会产出大量中间产物（临时脚本、
  下载缓存、草稿文件），它们不是给用户的交付物。只有让模型自己在收尾时明确「这次
  哪些文件是最终产物」，才能严格区分最终输出物与中间产物（用户诉求：只对最终输出
  物做文件单渲染 + 自动预览）。
- 本工具是纯声明口，不碰沙箱、无副作用：真正的「捕获」在 worker 事件循环里做
  （见 arq_tasks/tasks/harness.py：识别到 present_output 调用就发 turn.artifacts 帧
  并随 ASSISTANT 行落库），call 只需回一句人话给模型确认。

路径口径：只接受 /workspace 前缀的沙箱绝对路径（与 sandbox_file_service.WORKSPACE_ROOT
一致）；非法/越界路径直接丢弃，绝不写入 artifacts。校验集中在 normalize_artifacts，
工具与 worker 共用同一份，避免两套口径。

本模块只被 arq worker import（同 harness_agent / kb_search_tool 的口径）。
"""
from __future__ import annotations

import posixpath
from typing import Any

from agentscope.message import TextBlock
from agentscope.permission import (
    PermissionBehavior, PermissionContext, PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk

from service.service_harness.agent.tools.purpose import PURPOSE_DESCRIPTION

# 沙箱工作区绝对根（与后端 WORKSPACE_ROOT 常量对齐；工具侧不 import service 层，
# 避免把纯 agent 依赖拉到文件服务上，这里只认这一个字符串前缀）
WORKSPACE_PREFIX = "/workspace/"

# 单轮最多登记多少个输出物（防模型一口气把整个工作区列进来）
MAX_OUTPUT_FILES = 20


def normalize_artifacts(files: Any) -> list[dict]:
    """present_output 入参 files → 干净的输出物引用 [{name,path,description}]。

    只保留 /workspace 前缀、不含 .. 越界的绝对路径；name 缺省取路径末段；按 path
    去重、限量。工具 call 用它算「登记了几份」，worker 用它拼 artifacts——两处同一
    份校验，发帧与落库口径才不会跟回放对不上。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for f in files or []:
        if not isinstance(f, dict):
            continue
        raw = str(f.get("path") or "").strip().replace("\\", "/")
        if not raw.startswith(WORKSPACE_PREFIX):
            continue
        if ".." in raw.split("/"):
            continue
        if raw in seen:
            continue
        name = str(f.get("name") or "").strip() or posixpath.basename(raw)
        description = str(f.get("description") or "").strip()
        seen.add(raw)
        out.append({"name": name, "path": raw, "description": description})
        if len(out) >= MAX_OUTPUT_FILES:
            break
    return out


# 手写 input_schema（不用 pydantic.model_json_schema）：嵌套 list[object] 经
# model_json_schema 会产出 $defs/$ref，虽多数 jsonschema 实现能解析，但显式字典
# 更稳、也让模型看到每一项的确切字段。
_PRESENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "maxItems": MAX_OUTPUT_FILES,
            "description": (
                "本轮最终交付给用户的文件清单（只放最终产物，不要放中间/临时文件；"
                "没有文件产物就完全别调本工具）。"),
            "items": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "沙箱工作区绝对路径，必须以 /workspace/ 开头。",
                    },
                    "name": {
                        "type": "string",
                        "description": "展示用文件名，可留空；留空时取路径最后一段。",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话说明这个文件是什么/给用户看什么（可留空）。",
                    },
                },
                "required": ["path"],
            },
        },
        "purpose": {
            "type": "string",
            "description": PURPOSE_DESCRIPTION,
        },
    },
    "required": ["files"],
}


class PresentOutput(ToolBase):
    """登记本轮最终交付文件（纯声明，不执行任何沙箱动作）。"""

    name: str = "present_output"

    description: str = (
        "在你即将结束本轮、且任务确实产出了交付给用户的文件时调用，把这些文件的"
        "沙箱绝对路径（/workspace/ 下）登记进来，前端会在对话里渲染成可点击文件卡片"
        "并自动在右侧工作区预览。只登记最终产物：中间脚本、临时数据、下载缓存都不算。"
        "没有交付文件就不要调用本工具。")

    input_schema: dict[str, Any] = _PRESENT_OUTPUT_SCHEMA

    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False

    async def check_permissions(
            self,
            tool_input: dict[str, Any],
            context: PermissionContext,
    ) -> PermissionDecision:
        # 纯声明、无副作用、不改沙箱：恒放行（它只是把路径报给前端，不执行任何东西）
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="present_output 仅登记交付文件路径，无副作用。",
        )

    async def call(self, files: list | None = None, purpose: str = ""):
        # purpose 只给用户当工具行标题看，登记本身不用它（同内置工具的约定）
        _ = purpose
        cleaned = normalize_artifacts(files)
        if not cleaned:
            return ToolChunk(
                content=[TextBlock(
                    text="没有有效的交付文件（路径必须是 /workspace 下的绝对路径），"
                         "本次未登记。")],
            )
        listed = "、".join(a["name"] for a in cleaned)
        return ToolChunk(
            content=[TextBlock(text=f"已登记 {len(cleaned)} 个最终交付文件：{listed}")],
            metadata={"artifacts": cleaned},
        )
