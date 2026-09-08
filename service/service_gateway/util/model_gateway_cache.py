# -*- coding: utf-8 -*-
"""模型网关 Redis 缓存访问层（双层 hash 结构，与模块限流风格统一）：

- model_rate_limit_config（外层 hash）：field=model_id，value=模型路由配置 JSON
  (model_id/name/category/model_name/provider/base_url/is_direct/suffixes/rate_limit_qps/status)
- model_rate_limit（外层 hash）：field=api_key，value=JSON
  {model_id, user_id, apply_id, create_time}（比裸 model_id 多存元数据，支持按用户/模型批量吊销）

一致性模型：MySQL 为唯一数据源，Redis 是可重建缓存。
本模块只负责 Redis 读写，写入失败不抛异常（重试 + 日志），
由 service_system 启动/手动全量对账（ModelService.rebuild_cache）保证最终一致。
安全约定：管理端密钥（api_key）不进入 Redis，网关转发时回查 MySQL。
"""
import json
from datetime import datetime
from typing import Optional

from common.common_constants.constant import (
    PREFIX_MODEL_RATE_LIMIT,
    PREFIX_MODEL_RATE_LIMIT_CONFIG,
)
from common.common_log.log_init import log
from common.common_redis.redis import client

# 写入失败重试次数（不阻塞主业务流，最终由对账兜底）
_RETRY = 3


def _is_mapping_value(field: str, value, **filters) -> bool:
    """按 value JSON 中的字段过滤（用于批量吊销定位）"""
    if not isinstance(value, str):
        return False
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    for k, v in filters.items():
        if data.get(k) != v:
            return False
    return True


class ModelGatewayCache:
    """模型网关缓存：配置写入/读取、api-key 映射写入/吊销/读取"""

    # ==================== 模型路由配置（model_rate_limit_config） ====================

    @staticmethod
    async def write_config(config: dict) -> None:
        """写/刷新单个模型的路由配置（field=model_id）。失败重试，最终由对账兜底。"""
        config = dict(config)
        model_id = config.get("model_id")
        if model_id is None:
            log.error("ModelGatewayCache.write_config: missing model_id")
            return
        value = json.dumps(config, ensure_ascii=False)
        for i in range(_RETRY):
            try:
                await client.hset(PREFIX_MODEL_RATE_LIMIT_CONFIG, {str(model_id): value})
                return
            except Exception as e:  # noqa: BLE001
                log.error(f"write model config to redis failed (try {i + 1}): {e}")
        log.error(f"write model config to redis give up: model_id={model_id}")

    @staticmethod
    async def remove_config(model_id: int) -> None:
        """删除模型路由配置（模型删除时调用）"""
        try:
            await client.client.hdel(PREFIX_MODEL_RATE_LIMIT_CONFIG, str(model_id))
        except Exception as e:  # noqa: BLE001
            log.error(f"remove model config from redis failed: model_id={model_id} {e}")

    @staticmethod
    async def get_config(model_id: int) -> Optional[dict]:
        """网关鉴权：按 model_id 读模型路由配置（不存在/异常返回 None）"""
        try:
            raw = await client.hget(PREFIX_MODEL_RATE_LIMIT_CONFIG, str(model_id))
        except Exception as e:  # noqa: BLE001
            log.error(f"get model config from redis failed: model_id={model_id} {e}")
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    # ==================== api-key 映射（model_rate_limit） ====================

    @staticmethod
    async def add_key(api_key: str, model_id: int, user_id: int = 0, apply_id: int = 0) -> None:
        """审批通过后登记 api-key → {model_id, user_id, apply_id, create_time}"""
        if not api_key:
            log.error("ModelGatewayCache.add_key: missing api_key")
            return
        value = json.dumps({
            "model_id": model_id,
            "user_id": user_id,
            "apply_id": apply_id,
            "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False)
        for i in range(_RETRY):
            try:
                await client.hset(PREFIX_MODEL_RATE_LIMIT, {api_key: value})
                return
            except Exception as e:  # noqa: BLE001
                log.error(f"add model api-key to redis failed (try {i + 1}): {e}")
        log.error(f"add model api-key to redis give up: api_key={api_key[:8]}**** model_id={model_id}")

    @staticmethod
    async def remove_key(api_key: str) -> None:
        """吊销单个 api-key"""
        try:
            await client.client.hdel(PREFIX_MODEL_RATE_LIMIT, api_key)
        except Exception as e:  # noqa: BLE001
            log.error(f"remove model api-key from redis failed: {e}")

    @staticmethod
    async def remove_keys(model_id: int = None, user_id: int = None, apply_id: int = None) -> int:
        """按条件批量吊销 api-key（支持 model_id/user_id/apply_id 组合过滤），返回吊销数量"""
        filters = {k: v for k, v in {
            "model_id": model_id, "user_id": user_id, "apply_id": apply_id}.items() if v is not None}
        if not filters:
            return 0
        try:
            mapping = await client.hgetall(PREFIX_MODEL_RATE_LIMIT)
        except Exception as e:  # noqa: BLE001
            log.error(f"scan model api-keys failed: {e}")
            return 0
        victims = [k for k, v in (mapping or {}).items()
                   if _is_mapping_value(k, v, **filters)]
        if victims:
            try:
                await client.client.hdel(PREFIX_MODEL_RATE_LIMIT, *victims)
                log.info(f"ModelGatewayCache: revoked {len(victims)} api-keys "
                         f"filters={filters}")
            except Exception as e:  # noqa: BLE001
                log.error(f"revoke model api-keys failed: {e}")
        return len(victims)

    @staticmethod
    async def find_key(api_key: str) -> Optional[dict]:
        """网关鉴权：api-key → {model_id, user_id, apply_id, create_time}，不存在返回 None"""
        try:
            raw = await client.hget(PREFIX_MODEL_RATE_LIMIT, api_key)
        except Exception as e:  # noqa: BLE001
            log.error(f"find model api-key failed: {e}")
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
