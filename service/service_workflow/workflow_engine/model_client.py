# -*- coding: utf-8 -*-
"""WorkflowModelClient：工作流引擎的统一模型调用入口。

兼容设计（详见分析文档第 4 节）：
- 对引擎/节点暴露 MaxKB 风格的极简接口：create() / stream() / invoke()
- 底层复用 DRAGON-AI 模型广场数据（tb_model）+ Redis 缓存（model_rate_limit_config），
  按供应商约定的 OpenAI 兼容格式直连 provider（路径 A）。
- 引擎不感知 provider 差异：全部走 /chat/completions（TEXT_GEN/MULTIMODAL）与
  /embeddings（EMBEDDING）、/rerank（RERANK，通用 JSON POST）。

可测性：ModelProvider 协议可替换（tests 用 MockModelProvider 覆盖 stream/invoke）。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from common.common_constants.model_constant import (
    MODEL_ENDPOINT_CHAT,
    MODEL_ENDPOINT_DEFAULT_CHAT,
    MODEL_ENDPOINT_DEFAULT_EMBEDDING,
    MODEL_ENDPOINT_DEFAULT_RERANK,
    MODEL_ENDPOINT_EMBEDDING,
    MODEL_ENDPOINT_RERANK,
)
from common.common_log.log_init import log
# 数据结构公共定义（common 层，直连/非直连双路由共用；此处 re-export 保持历史 import 兼容）
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
                        cfg.api_key = await self._load_api_key_from_mysql(model_id) or ""
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
            is_direct=int(data.get("is_direct", 1)),
            # 网关缓存的后缀为纯 url 字符串列表；自动匹配逻辑兼容 str/dict 两种格式
            suffixes=data.get("suffixes") or [],
            status=int(data.get("status", 1)),
        )

    async def _load_api_key_from_mysql(self, model_id: int) -> Optional[str]:
        if self._mysql is None:
            return None
        from sqlalchemy import select
        from service.service_system.models.model import Model
        try:
            async with self._mysql.get_session() as session:
                return (await session.execute(
                    select(Model.api_key).where(Model.id == model_id))).scalar()
        except Exception as e:  # noqa: BLE001
            log.error("[WorkflowModel] 回源 api_key 失败 model_id={}: {}", model_id, e)
            return None

    async def _load_from_mysql(self, model_id: int) -> Optional[ModelConfig]:
        """回源 MySQL（SQLAlchemy ORM，suffixes/model_params JSON 列自动反序列化）。"""
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
                    is_direct=row.is_direct or 0,
                    suffixes=row.suffixes or [],
                    model_params=row.model_params or {}, status=row.status,
                )
        except Exception as e:  # noqa: BLE001
            log.error("[WorkflowModel] MySQL 读模型失败 model_id={}: {}", model_id, e)
            return None


# ==================== 客户端 ====================


class WorkflowModelClient:
    """引擎唯一模型入口（角色对等 MaxKB 的 get_model_instance_by_model_workspace_id）。

    用法（节点执行器内）：
        client = await WorkflowModelClient.create(model_id, provider=my_provider)
        async for chunk in client.stream(messages, temperature=0.7):
            ...  # chunk.content / chunk.reasoning_content
    """

    _default_provider: Optional[ModelConfigProvider] = None

    def __init__(self, config: ModelConfig, http: Optional[httpx.AsyncClient] = None,
                 timeout: float = 300.0):
        self.config = config
        self._http = http
        self._timeout = timeout

    # ---------- 工厂 ----------

    @classmethod
    def set_default_provider(cls, provider: ModelConfigProvider) -> None:
        cls._default_provider = provider

    @classmethod
    async def create(cls, model_id: int, provider: Optional[ModelConfigProvider] = None,
                     http: Optional[httpx.AsyncClient] = None) -> "WorkflowModelClient":
        prov = provider
        if prov is None:
            raise ModelNotFoundError("模型配置源未初始化（生产环境应在服务启动时 set_default_provider）")
        config = await prov.get_model_config(model_id)
        if config is None:
            raise ModelNotFoundError(f"模型不存在: model_id={model_id}")
        if config.status != 1:
            raise ModelNotFoundError(f"模型已停用: model_id={model_id} ({config.name})")
        return cls(config, http=http)

    # ---------- 请求构造 ----------

    def _build_body(self, messages: list[ChatMessage], stream: bool, **kwargs) -> dict:
        body: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": [m.to_openai() for m in messages],
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        # 模型广场默认参数（低优先级）+ 节点级参数（高优先级）
        params = dict(self.config.model_params or {})
        for k in ("temperature", "top_p", "top_k", "max_tokens", "presence_penalty",
                  "frequency_penalty", "stop", "response_format", "tools", "tool_choice",
                  "extra_body"):
            if kwargs.get(k) is not None:
                params[k] = kwargs[k]
        extra = params.pop("extra_body", None)
        if isinstance(extra, dict):
            params.update(extra)
        body.update({k: v for k, v in params.items() if v is not None})
        return body

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=10.0))
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---------- 流式 ----------

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[StreamChunk]:
        """OpenAI 兼容流式对话。解析 SSE data: {...choices[0].delta}，透传 reasoning_content/usage。"""
        url = self.config.model_url()
        body = self._build_body(messages, stream=True, **kwargs)
        try:
            async with self._client().stream("POST", url, json=body,
                                             headers=self._headers()) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "ignore")
                    raise ModelInvokeError(
                        f"模型调用失败 HTTP {resp.status_code}: {text[:500]} (model={self.config.model_name})")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("usage")
                    choices = obj.get("choices") or []
                    if not choices:
                        if usage:
                            yield StreamChunk(usage=usage, raw=obj)
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    yield StreamChunk(
                        content=delta.get("content") or "",
                        reasoning_content=delta.get("reasoning_content") or "",
                        finish_reason=choice.get("finish_reason"),
                        usage=usage,
                        raw=obj,
                    )
        except httpx.HTTPError as e:
            raise ModelInvokeError(f"模型网络错误: {e} (model={self.config.model_name})") from e

    # ---------- 非流式 ----------

    async def invoke(self, messages: list[ChatMessage], **kwargs) -> InvokeResult:
        url = self.config.model_url()
        body = self._build_body(messages, stream=False, **kwargs)
        try:
            resp = await self._client().post(url, json=body, headers=self._headers())
            if resp.status_code != 200:
                raise ModelInvokeError(
                    f"模型调用失败 HTTP {resp.status_code}: {resp.text[:500]} (model={self.config.model_name})")
            obj = resp.json()
            choices = obj.get("choices") or [{}]
            message = choices[0].get("message") or {}
            return InvokeResult(
                content=message.get("content") or "",
                reasoning_content=message.get("reasoning_content") or "",
                usage=obj.get("usage") or {},
                raw=obj,
            )
        except httpx.HTTPError as e:
            raise ModelInvokeError(f"模型网络错误: {e} (model={self.config.model_name})") from e

    # ---------- 嵌入 / 重排（KNOWLEDGE_RETRIEVAL 等节点用） ----------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        url = self._endpoint_url(MODEL_ENDPOINT_EMBEDDING, MODEL_ENDPOINT_DEFAULT_EMBEDDING)
        body = {"model": self.config.model_name, "input": texts}
        resp = await self._client().post(url, json=body, headers=self._headers())
        if resp.status_code != 200:
            raise ModelInvokeError(f"embedding 失败 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json().get("data") or []
        return [item.get("embedding") or [] for item in data]

    async def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict]:
        url = self._endpoint_url(MODEL_ENDPOINT_RERANK, MODEL_ENDPOINT_DEFAULT_RERANK)
        body = {"model": self.config.model_name, "query": query, "documents": documents,
                "top_n": top_n}
        resp = await self._client().post(url, json=body, headers=self._headers())
        if resp.status_code != 200:
            raise ModelInvokeError(f"rerank 失败 HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json().get("results") or []

    def _endpoint_url(self, keyword: str, default_suffix: str) -> str:
        if self.config.is_direct == 1:
            return self.config.base_url
        suffix = default_suffix
        for s in self.config.suffixes or []:
            u = s.get("url", "") if isinstance(s, dict) else str(s)
            if keyword in u:
                suffix = u
                break
        return self.config.base_url.rstrip("/") + suffix
