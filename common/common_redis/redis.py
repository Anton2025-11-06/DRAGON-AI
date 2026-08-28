import asyncio
import json
from typing import Optional, Any
from redis.asyncio import Redis
from common.common_log.log_init import log


class AsyncRedisClient:
    _instance: Optional["AsyncRedisClient"] = None
    _redis: Optional[Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


    async def init(self, host: str, port: int, password: str = "", db: int = 0, decode_resp: bool = False,
                   max_connections=50):
        try:
            self._redis = Redis(
                host=host,
                port=port,
                password=password if password else None,
                db=db,
                decode_responses=decode_resp,
                socket_timeout=10,
                retry_on_timeout=True,
                max_connections=max_connections
            )
            await self._redis.ping()
            log.info(f"Async Redis connect success: {host}:{port} db={db}")
        except Exception as e:
            log.info(f"Async Redis connect failed: {str(e)}")
            raise e

    async def close(self):
        await self._redis.aclose()
        log.info("Async Redis connection closed")

    async def set(self, key: str, value: Any, expire: int = None):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if expire:
            await self._redis.setex(key, expire, value)
        else:
            await self._redis.set(key, value)

    async def get(self, key: str, to_dict: bool = False) -> Any:
        data = await self._redis.get(key)
        if not data:
            return None
        if to_dict:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data
        return data

    async def delete(self, *keys: str):
        await self._redis.delete(*keys)

    async def exists(self, key: str) -> int:
        return await self._redis.exists(key)

    async def incr(self, key: str, step: int = 1) -> int:
        return await self._redis.incrby(key, step)

    async def hset(self, name: str, mapping: dict, expire: int = None):
        await self._redis.hset(name, mapping=mapping)
        if expire:
            await self._redis.expire(name, expire)

    async def hgetall(self, name: str) -> dict:
        data = await self._redis.hgetall(name)
        # decode_responses=False 时返回 bytes：统一解码为 str，避免业务侧 JSON 解析失败
        if data and isinstance(next(iter(data)), bytes):
            return {k.decode("utf-8"): (v.decode("utf-8") if isinstance(v, bytes) else v)
                    for k, v in data.items()}
        return data

    async def lpush(self, key: str, *values):
        await self._redis.lpush(key, *values)

    async def rpop(self, key: str) -> Optional[str]:
        return await self._redis.rpop(key)

    @property
    def client(self) -> Redis:
        return self._redis

client = AsyncRedisClient()