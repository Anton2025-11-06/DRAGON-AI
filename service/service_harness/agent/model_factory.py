# -*- coding: utf-8 -*-
"""模型工厂：模型 id → agentscope 聊天模型实例。

取值口径（与 workflow LLM 节点同源，且不在对话热路径上碰 MySQL）：
  本轮只携带 model_id，模型的真实配置（provider/model_name/base_url/api_key/
  能力位）直接按 model_id 命中网关写入的 model_rate_limit_config（field=model_id，
  已含管理端 api_key），命中即用；缓存缺失（新登记/未同步）才回源 tb_model 兑底一次。
  worker 直连模型不走网关，递给厂家的必须是 model 侧真实凭据（不是平台虚拟 key，
  否则厂家 401）。

能力校验：模型必须启用且 supports_function_call=1——ReAct 循环第一步就是工具
  调用，能力位不够的模型跑第一轮就废，入口处按缓存里的能力位拦掉。
provider 分派：dashscope 走 DashScopeChatModel（原生 SDK）；openai/zhipu 走
  OpenAIChatModel（OpenAI 兼容，base_url 必配）。

授权（谁能用哪个模型）在可选清单接口（usable_models）按本人 apply 收口；
本模块不再逐轮回查 tb_model_apply——每轮 chat 都查 MySQL 会把库压垮，热路径不需要。

本模块只被 arq worker import（service 进程不依赖 agentscope，见
harness_pubsub 模块尾注释）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import SecretStr

from agentscope.credential import DashScopeCredential, OpenAICredential
from agentscope.model import DashScopeChatModel, OpenAIChatModel

from common.common_constants.constant import PREFIX_MODEL_RATE_LIMIT_CONFIG
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client as redis_client

from service.service_system.models.model import Model

# OpenAI 兼容通道的供应商（zhipu 的 chat API 是 OpenAI 兼容形态）
_OPENAI_COMPATIBLE_PROVIDERS = ("openai", "zhipu")

# 平台签发给用户的**虚拟** api-key 前缀（model_service.API_KEY_PREFIX）：这类 key
# 只能在网关侧换取真厂商，直递厂商必 401——管理端误填进 tb_model.api_key 时拦下。
_VIRTUAL_KEY_PREFIX = "mk_"


async def _load_model_config(model_id: int) -> Optional[dict]:
    """按 model_id 取模型真实配置（含厂家 api_key）：Redis 缓存优先，MySQL 兜底。

    与 workflow 的 ModelConfigProvider.get_model_config 同口径——网关在模型增删改时
    把整份路由配置（含 api_key/base_url/能力位）写进 model_rate_limit_config，
    这里命中即用；缓存缺失（新登记/未同步）时回源 tb_model，避免误报「模型不存在」。

    特例：命中了缓存但条目里没有 api_key（早期写入的路由配置、或管理端刚补key还没
    回写缓存），不能因此当成「模型未配密钥」——回源 MySQL 把真实厂家密钥补齐再返回。
    """
    async def _from_mysql() -> Optional[dict]:
        async with mysql_client.get_session() as session:
            row = await session.get(Model, int(model_id))
            if row is None:
                return None
            # 拷出字段再离开 session（ORM 属性离开 session 不可读）
            return {
                "model_id": row.id,
                "provider": row.provider,
                "model_name": row.model_name,
                "base_url": row.base_url or "",
                "api_key": row.api_key or "",
                "status": int(row.status or 0),
                "supports_function_call": int(row.supports_function_call or 0),
            }

    try:
        raw = await redis_client.hget(PREFIX_MODEL_RATE_LIMIT_CONFIG, str(model_id))
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            # 缓存里 status 非启用的不直接采用：回源拿最新状态再判停用与否
            if isinstance(data, dict) and int(data.get("status") or 0) == 1:
                # 缓存没带真实厂家密钥（旧条目/刚补key 未同步）：回源 MySQL 补齐
                if not str(data.get("api_key") or "").strip():
                    mysql_cfg = await _from_mysql()
                    if mysql_cfg is not None:
                        data = {**data, "api_key": mysql_cfg["api_key"]}
                return data
    except Exception as e:  # noqa: BLE001
        log.warning("harness model 读 Redis 配置失败 model_id={}: {}", model_id, e)

    return await _from_mysql()


async def build_model(model_id: int) -> tuple[Any, str]:
    """按模型 id 构造 agentscope 模型，返回 (模型实例, model_name 快照)。

    :param model_id: 本轮携带的模型 id（0 = 未选模型）
    :raises ValueError: 模型不存在/停用/能力不足/无厂家密钥
    """
    if not model_id:
        raise ValueError("尚未选择模型，请先在模型广场申请并选择一个已授权的模型")

    # 真实模型配置（api_key/base_url/能力位）按 model_id 走 Redis 缓存，未命中才回源 MySQL 兑底
    config = await _load_model_config(int(model_id))
    if config is None or int(config.get("status") or 0) != 1:
        raise ValueError("模型不存在或已停用")
    if int(config.get("supports_function_call") or 0) != 1:
        raise ValueError("该模型不支持工具调用，无法驱动智能体")
    provider = config.get("provider")
    model_name = config.get("model_name")
    base_url = config.get("base_url") or ""
    # 真实厂家密钥（apply 的 mk_xxx 是平台虚拟 key，不能递给厂家 API 否则 401）
    api_key = config.get("api_key")
    if not api_key:
        raise ValueError("模型未配置厂家密钥（请联系管理员在模型广场补填）")
    if str(api_key).startswith(_VIRTUAL_KEY_PREFIX):
        raise ValueError(
            "模型配置里存的是平台虚拟 api-key（mk_ 开头），不是厂家真实密钥——"
            "请在模型广场把该模型的「管理端密钥」改为厂商真实 API Key")

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
