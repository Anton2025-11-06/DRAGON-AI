import json

import httpx
from fastapi import Request
from pydantic import BaseModel, Field

from common.common_constants.constant import PREFIX_RATE_LIMIT_CONFIG, SERVICE_GATEWAY
from common.common_log.log_init import log
from common.common_redis.redis import client


class RateLimitConfigRequest(BaseModel):
    """单模块限流策略：bucket=模块桶名（/api/{bucket} 首段），limit=窗口内最大次数，window=窗口秒"""
    bucket: str = Field(..., min_length=1, max_length=64, description="模块桶名")
    limit: int = Field(..., ge=1, le=1000000, description="窗口内最大次数")
    window: int = Field(..., ge=1, le=86400, description="窗口秒数")
    enabled: bool = True


class RateLimitConfigService:
    """限流策略配置：读写 Redis hash（rate_limit_config），发布时通知网关刷新内存"""

    @staticmethod
    async def list_configs() -> list:
        """读取全部已配置策略（hash field=模块，value=JSON 策略）"""
        raw = await client.hgetall(PREFIX_RATE_LIMIT_CONFIG)
        items = []
        for bucket, value in (raw or {}).items():
            try:
                policy = json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError):
                policy = {}
            items.append({
                "bucket": bucket,
                "limit": int(policy.get("limit") or 0),
                "window": int(policy.get("window") or 60),
                "enabled": bool(policy.get("enabled", True)),
            })
        items.sort(key=lambda x: x["bucket"])
        return items

    @staticmethod
    async def save_config(request: RateLimitConfigRequest) -> None:
        """新增/更新单模块策略并写 Redis"""
        await client.hset(PREFIX_RATE_LIMIT_CONFIG,
                          {request.bucket: json.dumps({
                              "limit": request.limit,
                              "window": request.window,
                              "enabled": request.enabled,
                          }, ensure_ascii=False)})
        log.info(f"Rate limit config saved: {request.bucket} -> {request.limit}/{request.window}")

    @staticmethod
    async def delete_config(bucket: str) -> bool:
        """删除单模块策略（回到无限流）"""
        if not await client.exists(PREFIX_RATE_LIMIT_CONFIG):
            return False
        removed = await client.client.hdel(PREFIX_RATE_LIMIT_CONFIG, bucket)
        log.info(f"Rate limit config removed: {bucket}")
        return removed > 0

    @staticmethod
    async def publish(request: Request) -> dict:
        """
        发布限流配置：通知所有存活网关从 Redis 重新加载内存策略。
        单个网关失败不中断其余网关，返回成功/失败网关数量与发布策略数。
        网关实例地址通过 Nacos 服务发现获取（与网关转发同一来源，保证地址可达）。
        """
        nacos_service = request.app.state.nacos_service
        targets = await nacos_service.get_all_healthy_instance(SERVICE_GATEWAY)
        if not targets:
            raise ValueError("网关服务无可用实例，发布失败")
        result = {
            "success_count": 0,
            "failed_count": 0,
            "policy_count": 0,
            "policies": {},
        }
        # trust_env=False：跳过本机系统代理，确保内网直连
        async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
            for ip, port in targets:
                url = f"http://{ip}:{port}/internal/rate-limit/reload"
                try:
                    resp = await http.post(url, timeout=3)
                    if resp.status_code != 200:
                        result["failed_count"] += 1
                        log.error(f"Rate limit publish failed (HTTP {resp.status_code}): {ip}:{port}")
                        continue
                    # 网关 reload 响应携带其从 Redis 加载的策略表（所有网关同源，内容一致）
                    policies = (resp.json() or {}).get("data", {}).get("policies") or {}
                    result["success_count"] += 1
                    result["policy_count"] = len(policies)
                    result["policies"] = policies
                    log.info(f"Rate limit published to gateway {ip}:{port}, policies={len(policies)}")
                except Exception as exc:
                    result["failed_count"] += 1
                    log.error(f"Rate limit publish failed: {ip}:{port}, err={exc}")
        return result


