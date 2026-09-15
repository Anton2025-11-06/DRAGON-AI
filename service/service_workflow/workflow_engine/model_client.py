# -*- coding: utf-8 -*-
"""WorkflowModelClient：工作流引擎的统一模型调用入口（全部经 common_model 类型化直连）。

- create() 加载配置（tb_model + Redis 缓存 model_rate_limit_config）；
- acall()/chat()/rerank() 按 (category, provider) 交给 common_model 对应子类
  直连本厂商端点（无跨厂商桥接、无手写 httpx 拼 URL）。
- 【设计变更 2026-09】旧 stream/invoke/embed/rerank 直连 httpx 路径已废弃删除：
  端点由 common_model 各类型子类自拼，模型登记只需 base_url（厂商接口基础地址）
  + provider + category。

可测性：ModelConfigProvider 可替换（tests 用 mock 覆盖 get_model_config）。
"""
from __future__ import annotations

import json
from typing import Optional

from common.common_constants.model_constant import (
    MODEL_CATEGORY_TEXT_GEN, MT_TEXT_RERANK, MT_TEXT_TO_TEXT,
)
from common.common_log.log_init import log
# 数据结构公共定义（common 层）；此处 re-export 保持历史 import 兼容
from common.common_model.base import ModelResult
from common.common_model.model_types import (
    ChatMessage,
    InvokeResult,
    ModelConfig,
    ModelInvokeError,
    ModelNotFoundError,
    StreamChunk,
)


# ==================== 配置源协议 ====================
class ModelConfigProvider:

    def __init__(self, redis_client=None, mysql_client=None):
        # 延迟导入避免测试环境强依赖
        self._redis = redis_client
        self._mysql = mysql_client

    async def get_model_config(self, model_id: int) -> Optional[ModelConfig]:
        raw = None
        # 1. Redis 缓存（不含 api_key）
        if self._redis is not None:
            try:
                from common.common_constants.constant import PREFIX_MODEL_RATE_LIMIT_CONFIG
                raw = await self._redis.hget(PREFIX_MODEL_RATE_LIMIT_CONFIG, str(model_id))
                if raw:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    cfg = self._from_cache(data)
                    if cfg is not None:
                        secrets = await self._load_secrets_from_mysql(model_id)
                        cfg.api_key = secrets.get("api_key") or ""
                        # 缓存条目可能由旧版本写入（不含 model_params / 能力位）：回源补齐，
                        # 否则模型管理登记的常用参数与流式/思考开关会在命中缓存时被静默丢弃
                        if not cfg.model_params and secrets.get("model_params"):
                            cfg.model_params = secrets["model_params"]
                        for flag in ("supports_stream", "supports_thinking"):
                            if data.get(flag) is None:
                                setattr(cfg, flag, bool(secrets.get(flag)))
                        if cfg.status == 1:
                            return cfg
            except Exception as e:  # noqa: BLE001
                log.warning("[WorkflowModel] redis 读模型配置失败 model_id={}: {}", model_id, e)
        # 2. 回源 MySQL
        return await self._load_from_mysql(model_id)

    def _from_cache(self, data: dict) -> Optional[ModelConfig]:
        if not isinstance(data, dict):
            return None
        return ModelConfig(
            model_id=int(data.get("model_id", 0)),
            name=data.get("name", ""),
            category=data.get("category", MODEL_CATEGORY_TEXT_GEN),
            provider=data.get("provider", ""),
            model_name=data.get("model_name", ""),
            base_url=data.get("base_url", "") or "",
            model_params=data.get("model_params") or {},
            supports_stream=bool(data.get("supports_stream")),
            supports_thinking=bool(data.get("supports_thinking")),
            status=int(data.get("status", 1)),
        )

    async def _load_secrets_from_mysql(self, model_id: int) -> dict:
        """回源 MySQL 取凭据与不随缓存走的字段（api_key、常用参数、能力位）。"""
        if self._mysql is None:
            return {}
        from sqlalchemy import select
        from service.service_system.models.model import Model
        try:
            async with self._mysql.get_session() as session:
                row = (await session.execute(
                    select(Model.api_key, Model.model_params,
                           Model.supports_stream, Model.supports_thinking)
                    .where(Model.id == model_id))).first()
                if row is None:
                    return {}
                return {
                    "api_key": row[0],
                    "model_params": row[1] or {},
                    "supports_stream": bool(row[2]),
                    "supports_thinking": bool(row[3]),
                }
        except Exception as e:  # noqa: BLE001
            log.error("[WorkflowModel] 回源 api_key/model_params/能力位 失败 model_id={}: {}", model_id, e)
            return {}

    async def _load_from_mysql(self, model_id: int) -> Optional[ModelConfig]:
        """回源 MySQL（SQLAlchemy ORM，model_params JSON 列自动反序列化）。"""
        if self._mysql is None:
            return None
        from sqlalchemy import select
        from service.service_system.models.model import Model
        try:
            async with self._mysql.get_session() as session:
                row = (await session.execute(
                    select(Model).where(Model.id == model_id))).scalar_one_or_none()
                if row is None:
                    return None
                return ModelConfig(
                    model_id=row.id, name=row.name, category=row.category,
                    provider=row.provider, model_name=row.model_name,
                    base_url=row.base_url or "", api_key=row.api_key or "",
                    model_params=row.model_params or {}, status=row.status,
                    supports_stream=bool(row.supports_stream),
                    supports_thinking=bool(row.supports_thinking),
                )
        except Exception as e:  # noqa: BLE001
            log.error("[WorkflowModel] MySQL 读模型失败 model_id={}: {}", model_id, e)
            return None


# ==================== 客户端 ====================


class WorkflowModelClient:
    """引擎唯一模型入口（角色对等 MaxKB 的 get_model_instance_by_model_workspace_id）。

    用法（节点执行器内）：
        client = await WorkflowModelClient.create(model_id, provider=my_provider)
        # 按类型分发（LLM 节点用）：
        category, inst = client.acall()
        r = await inst.ainvoke(**kwargs)
        # 对话类便捷（问题分类器/参数提取器用）：
        r = await client.chat(prompt="...", temperature=0.0)
        # 重排便捷（知识检索用）：
        scores = await client.rerank(query, documents, top_n=5)
    """

    _default_provider: Optional[ModelConfigProvider] = None

    def __init__(self, config: ModelConfig):
        self.config = config

    # ---------- 工厂 ----------

    @classmethod
    def set_default_provider(cls, provider: ModelConfigProvider) -> None:
        cls._default_provider = provider

    @classmethod
    async def create(cls, model_id: int, provider: Optional[ModelConfigProvider] = None,
                     http=None) -> "WorkflowModelClient":
        prov = provider
        if prov is None:
            raise ModelNotFoundError("模型配置源未初始化（生产环境应在服务启动时 set_default_provider）")
        config = await prov.get_model_config(model_id)
        if config is None:
            raise ModelNotFoundError(f"模型不存在: model_id={model_id}")
        if config.status != 1:
            raise ModelNotFoundError(f"模型已停用: model_id={model_id} ({config.name})")
        return cls(config)

    # ---------- common_model 通用分发（按类型直连，非桥接） ----------

    def acall(self, category: Optional[str] = None):
        """返回对应能力类型的 common_model 实例（由 config.provider 动态解析）。

        category 缺省取模型登记的能力类型；LLM 节点据此拿到实例后按类型 ainvoke/astream。
        """
        from common.common_model import entry as cm_entry
        cat = category or self.config.category
        model_config = cm_entry.config_from_row(self.config)
        return cat, cm_entry.instantiate(cat, model_config)

    # ---------- 对话便捷方法（问题分类器 / 参数提取器等文本类节点复用） ----------

    async def chat(self, messages: Optional[list] = None, prompt: Optional[str] = None,
                   **kwargs) -> ModelResult:
        """强制以 text_to_text 直连调用（返回 common_model 的 ModelResult）。

        messages 支持 ChatMessage 对象或 OpenAI dict；kwargs 透传 temperature/max_tokens/
        response_format/thinking 等采样参数给对应子类。
        """
        if messages and isinstance(messages[0], ChatMessage):
            messages = [m.to_openai() for m in messages]
        _, inst = self.acall(MT_TEXT_TO_TEXT)
        return await inst.ainvoke(messages=messages, prompt=prompt, **kwargs)

    async def rerank(self, query: str, documents: list, top_n: int = 5) -> list:
        """强制以 text_rerank 直连重排，返回 [{'index','relevance_score'}] 列表。"""
        _, inst = self.acall(MT_TEXT_RERANK)
        r = await inst.ainvoke(query=query, documents=documents, top_n=top_n)
        return r.scores or []
