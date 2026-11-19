# -*- coding: utf-8 -*-
"""编排成员解析（团队/目标/SOP 共用）：OrchMemberSpec → 落库快照 dict。

口径（计划 §6 克隆过滤）：
- 给了 sourceAgentId → 从 tb_orch_agent 取该行 name/system_prompt/mounts 作初值，
  请求里显式提供的字段（name/systemPrompt/mounts）覆盖之（页面自定义/改配置）；
- 未给 sourceAgentId → 纯自定义，全部取请求字段；
- 无论克隆还是自定义，最终 mounts 都过 filter_mounts_for_user：逐成员把当前用户
  无 use 权的能力 id 静默剔除，保证「下拉选得中即运行可用」（与 picker/mount_loader
  同一口径）。成员配置改动只落各自表 config_json 快照，绝不回写 tb_orch_agent。
"""
from __future__ import annotations

from typing import Optional

from service.service_harness.schemas.orch_schema import OrchMemberSpec
from service.service_harness.services.orch_agent_service import OrchAgentService


async def resolve_member(login_user: dict, spec: OrchMemberSpec) -> dict:
    """把一个成员规格解析成可持久化/可运行的快照 dict。

    返回 {source_agent_id, name, description, system_prompt, mounts}；
    mounts 已完成克隆来源合并 + use 口径过滤，name/mounts 保证非空可用。
    """
    source_id = spec.sourceAgentId
    name = spec.name
    description = spec.description
    system_prompt = spec.systemPrompt
    mounts: Optional[dict] = spec.mounts.model_dump() if spec.mounts is not None else None

    if source_id:
        src = await OrchAgentService.get_row(source_id)
        if src is not None:
            # 克隆初值：请求未覆盖的字段回落到来源单智能体
            name = name or src.name
            if system_prompt is None:
                system_prompt = src.system_prompt
            if mounts is None:
                mounts = dict(src.mounts or {})

    # 克隆过滤（use 口径）：无论来源是克隆还是纯自定义，都剔除当前用户无权的能力
    mounts = await OrchAgentService.filter_mounts_for_user(login_user, mounts)
    return {
        "source_agent_id": source_id,
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "mounts": mounts,
    }
