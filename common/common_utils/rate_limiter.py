from datetime import datetime

from common.common_constants.constant import PREFIX_RATE_LIMIT, PREFIX_MODEL_RATE_LIMIT_QPS
from common.common_log.log_init import log
from common.common_redis.redis import client

# Lua 原子脚本：计数 + 首次设置过期（含外层活跃索引）。
# Redis 数据结构（双层 hash + 字段级 TTL，Redis 7.4+ HEXPIRE）：
#   rate_limit            外层 hash：field=ip，value=1（活跃 ip 索引）
#   {ip}        内层 hash：field=策略桶（bucket），value=请求次数，每个 field 独立过期
# 整个脚本在 Redis 单线程内原子执行，不存在计数与过期分离的窗口失效问题。
_INCR_AND_EXPIRE_SCRIPT = """
local count = redis.call('hincrby', KEYS[2], ARGV[2], 1)
if count == 1 then
    redis.call('hexpire', KEYS[2], ARGV[3], 'FIELDS', 1, ARGV[2])
    redis.call('hexpire', KEYS[1], ARGV[3], 'FIELDS', 1, ARGV[1])
end
return count
"""


_INCR_AND_EXPIRE_SCRIPT_QPS = """
local count = redis.call('hincrby', KEYS[2], ARGV[2], 1)
if count == 1 then
    redis.call('hexpire', KEYS[2], ARGV[3], 'FIELDS', 1, ARGV[2])
    redis.call('hexpire', KEYS[1], ARGV[3], 'FIELDS', 1, ARGV[1])
end
return count
"""



class RateLimiter:
    """Redis 固定窗口限流器：rate_limit 双层 hash，策略字段级 TTL，跨实例生效"""

    @staticmethod
    async def check(ip: str, bucket: str, limit: int, window: int = 60) -> bool:
        """
        判断是否放行
        :param ip: 客户端 ip（外层 hash 的 field，限流维度标识）
        :param bucket: 策略桶（内层 hash 的 field，如 login/system/global）
        :param limit: 窗口内最大次数
        :param window: 窗口秒数（仅作用于本次计数的字段）
        :return: True 放行，False 超限
        """

        redis = client.client
        try:
            count = await redis.eval(_INCR_AND_EXPIRE_SCRIPT, 2,
                                     PREFIX_RATE_LIMIT,
                                     PREFIX_RATE_LIMIT + ":" + ip,
                                     ip,
                                     bucket,
                                     window)
        except Exception as e:  # noqa: BLE001
            # Redis 连接池满/超时/抖动时降级放行（fail-open）并告警，避免限流器故障拖垮整个网关；
            # 防爆破场景为“尽力而为”降级，Redis 恢复后自动回归严格限流
            log.warning(f"RateLimiter.check failed (ip={ip} bucket={bucket}): {str(e)} — fail-open")
            return True
        return count <= limit

    # ==================== AI 模型网关限流 ====================

    @staticmethod
    async def check_model_qps(model_id: int, qps_limit: int) -> bool:
        """
        模型 QPS 限流（每秒固定窗口，双层 hash + 字段级 TTL）：
          外层 model_rate_limit_qps    field=model_id（活跃索引）
          内层 model_rate_limit_qps:{model_id}  field=qps，value=每秒次数，60 秒后自动过期
        :param model_id: 模型 id
        :param qps_limit: 每秒最大请求数（0=不限，调用方保证 >0）
        :return: True 放行，False 超限
        """
        redis = client.client
        try:
            count = await redis.eval(
                _INCR_AND_EXPIRE_SCRIPT_QPS, 2,
                PREFIX_MODEL_RATE_LIMIT_QPS,
                PREFIX_MODEL_RATE_LIMIT_QPS + ":" + str(model_id),
                str(model_id),
                "qps",
                60,  # 60 秒窗口
            )
        except Exception as e:  # noqa: BLE001
            log.warning(f"RateLimiter.check_model_qps failed (model={model_id}): {str(e)} — fail-open")
            return True
        return count <= qps_limit

