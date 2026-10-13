# -*- coding: utf-8 -*-
"""挂载组装统一入口：校验（use 口径）→ 三桥 → 技能种入 → MountBundle。

挂载校验与前端 picker 完全同源：可见集合 = options 四口（HarnessOptionsService
kb/skill 轻量查 + McpServerService/ToolService 的 options，全部 ACTION_USE）。
越权或已失效的挂载**静默跳过并记日志**——不炸整轮对话（快照本身不是信任
边界，真正拦住越权的是这道过滤：模型收不到工具就等于被拒）。

MountBundle 是 worker 组装期的中间产物：
- tools：三桥产出的 agentscope 工具实例（并入 Toolkit）；
- skill_paths：技能解压后的本地目录（workspace 构造参数，种入沙箱）；
- summary：挂载摘要（系统提示词追加段用，让模型知道本轮有哪些平台能力）。

本模块只被 arq worker import（同 harness_agent 的口径）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.common_log.log_init import log


@dataclass
class MountBundle:
    """一轮对话的平台能力挂载产物（worker 组装 Toolkit / workspace 的素材）。"""

    tools: list[Any] = field(default_factory=list)
    skill_paths: list[str] = field(default_factory=list)
    # 挂载摘要（挂载了什么给模型看）：kbNames / mcpNames / toolNames / skillNames
    summary: dict[str, list[str]] = field(default_factory=dict)


async def _visible_ids(kind: str, login_user: dict) -> dict[int, str]:
    """use 口径的可见集合 {id: name}（与前端 picker 同一个数据源）。

    查询失败降级为空集合（等价于「本轮没有可挂能力」），不让一轮对话失败。
    """
    from service.service_harness.services.options_service import (
        HarnessOptionsService,
    )
    try:
        rows = await HarnessOptionsService.options(login_user, kind)
        return {int(r["id"]): str(r.get("name") or r["id"]) for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("harness mount visible query failed: kind={} {}", kind, e)
        return {}


async def _visible_meta(kind: str, login_user: dict) -> dict[int, dict]:
    """use 口径的可见集合 {id: {name, description}}（工具描述组装用）。

    description 是 agent 选择工具的关键依据，缺失会导致模型不知道该工具能干什么。
    """
    from service.service_harness.services.options_service import (
        HarnessOptionsService,
    )
    try:
        rows = await HarnessOptionsService.options(login_user, kind)
        return {int(r["id"]): {
            "name": str(r.get("name") or r["id"]),
            "description": str(r.get("description") or "").strip(),
        } for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("harness mount visible query failed: kind={} {}", kind, e)
        return {}


def _intersect(ids: list[int], visible: dict[int, Any], kind: str) -> list[int]:
    """请求挂载 ∩ 可见集合；越权/失效的记一条日志（静默跳过，不炸整轮）。"""
    wanted = [int(i) for i in ids or []]
    kept = [i for i in wanted if i in visible]
    dropped = [i for i in wanted if i not in visible]
    if dropped:
        log.warning("harness mount rejected ({}): {} not in visible {}",
                    kind, dropped, sorted(visible))
    return kept


async def load_mounted(login_user: dict, mounts: dict) -> MountBundle:
    """按挂载快照组装本轮平台能力（入口：run_harness_turn 组装期调用一次）。"""
    from service.service_harness.agent.tools import (
        mcp_tools, platform_tools, skill_seed,
    )
    from service.service_harness.agent.tools.kb_search_tool import (
        make_kb_search_tool,
    )

    bundle = MountBundle()
    mounts = mounts or {}
    if not any(mounts.get(key) for key in
               ("kbIds", "mcpIds", "toolIds", "skillIds")):
        return bundle

    # ---- 知识库 → kb_search 工具（一个工具覆盖全部挂载库）----
    kb_meta = await _visible_meta("kb", login_user)
    kb_effective = _intersect(mounts.get("kbIds") or [], kb_meta, "kb")
    if kb_effective:
        # 把知识库的 description 拼进工具描述，让 agent 知道每个库是干什么的
        kb_names = {i: kb_meta[i]["name"] for i in kb_effective}
        kb_desc_parts = [f"{kb_meta[i]['name']}：{kb_meta[i]['description']}"
                         for i in kb_effective if kb_meta[i].get("description")]
        bundle.tools.append(make_kb_search_tool(
            login_user, kb_effective, kb_names,
            kb_descriptions=kb_desc_parts))
        bundle.summary["kbNames"] = [kb_meta[i]["name"] for i in kb_effective]

    # ---- MCP 连接 → 每个远端工具一个实例 ----
    mcp_visible = await _visible_ids("mcp", login_user)
    mcp_effective = _intersect(mounts.get("mcpIds") or [], mcp_visible, "mcp")
    if mcp_effective:
        # _intersect 已按可见集合过滤；build_mcp_tools 再做一次连通性/停用检查
        tools, names = await mcp_tools.build_mcp_tools(mcp_effective)
        bundle.tools.extend(tools)
        if names:
            bundle.summary["mcpNames"] = names

    # ---- 平台工具 → 每个工具一个实例 ----
    tool_visible = await _visible_ids("tool", login_user)
    tool_effective = _intersect(mounts.get("toolIds") or [], tool_visible, "tool")
    if tool_effective:
        tools, names = await platform_tools.build_platform_tools(tool_effective)
        bundle.tools.extend(tools)
        if names:
            bundle.summary["toolNames"] = names

    # ---- 技能 → zip 解压 → workspace skill_paths ----
    skill_visible = await _visible_ids("skill", login_user)
    skill_effective = _intersect(mounts.get("skillIds") or [], skill_visible, "skill")
    if skill_effective:
        paths, names = await skill_seed.seed_skills(login_user, skill_effective)
        bundle.skill_paths.extend(paths)
        if names:
            bundle.summary["skillNames"] = names

    log.info("harness mounts loaded: kb={} mcp={} tool={} skill={} tools={}",
             kb_effective, mcp_effective, tool_effective, skill_effective,
             len(bundle.tools))
    return bundle
