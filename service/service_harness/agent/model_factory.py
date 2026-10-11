# -*- coding: utf-8 -*-
"""模型工厂：模型广场授权记录 → agentscope 聊天模型实例。

授权链（tb_model_apply + tb_model）：
- apply 必须本人且 status=1（审批通过后签发的 api_key 在 apply 行上，不在模型行
  ——模型行的 api_key 是管理端密钥，不会发给使用者）；
- 模型必须启用且 supports_function_call=1：ReAct 循环第一步就是工具调用，
  能力位不够的模型跑第一轮就废，入口处拦掉；
- provider 分派：dashscope 走 DashScopeChatModel（原生 SDK）；openai/zhipu 走
  OpenAIChatModel（OpenAI 兼容，base_url 必配）。

本模块只被 arq worker import（service 进程不依赖 agentscope，见
harness_pubsub 模块尾注释）。
"""
from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from agentscope.credential import DashScopeCredential, OpenAICredential
from agentscope.model import DashScopeChatModel, OpenAIChatModel

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

from service.service_system.models.model import Model, ModelApply

# OpenAI 兼容通道的供应商（zhipu 的 chat API 是 OpenAI 兼容形态）
_OPENAI_COMPATIBLE_PROVIDERS = ("openai", "zhipu")


async def build_model(model_apply_id: int, login_user: dict) -> tuple[Any, str]:
    """按授权记录构造 agentscope 模型，返回 (模型实例, model_name 快照)。

    :param model_apply_id: 会话行上的授权记录 id（0 = 未选模型）
    :param login_user: 发消息用户的 JWT 载荷（user_id 作归属校验）
    :raises ValueError: 授权/能力校验不过（文案直接面向调用方）
    """
    if not model_apply_id:
        raise ValueError("尚未选择模型，请先在会话设置中选择（模型广场申请授权后可选）")
    user_id = int(login_user.get("user_id") or 0)

    async with mysql_client.get_session() as session:
        apply_row = await session.get(ModelApply, int(model_apply_id))
        if apply_row is None or int(apply_row.user_id) != user_id:
            raise ValueError("模型授权不存在或不属于当前用户")
        if int(apply_row.status or 0) != 1:
            raise ValueError("模型授权未通过审批（模型广场提交申请）")
        model_row = await session.get(Model, int(apply_row.model_id))
        if model_row is None or int(model_row.status or 0) != 1:
            raise ValueError("模型不存在或已停用")
        if int(model_row.supports_function_call or 0) != 1:
            raise ValueError("该模型不支持工具调用，无法驱动智能体")
        # 拷出字段再离开 session（ORM 属性离开 session 不可读）
        provider = model_row.provider
        model_name = model_row.model_name
        base_url = model_row.base_url
        api_key = apply_row.api_key

    if not api_key:
        raise ValueError("模型授权未签发 API Key（请联系管理员补发）")

    if provider == "dashscope":
        model = DashScopeChatModel(
            credential=DashScopeCredential(api_key=SecretStr(api_key)),
            model=model_name,
        )
    elif provider in _OPENAI_COMPATIBLE_PROVIDERS:
        if not base_url:
            raise ValueError(f"模型未配置 base_url（{provider} 通道必需）")
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key=SecretStr(api_key),
                                        base_url=base_url),
            model=model_name,
        )
    else:
        raise ValueError(f"不支持的模型供应商：{provider}")

    log.info("harness model built: provider={} model={}", provider, model_name)
    return model, model_name
