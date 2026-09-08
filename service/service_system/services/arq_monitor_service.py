# -*- coding: utf-8 -*-
"""arq 队列指标与 worker 节点健康监控(供 system 模块监控接口与前端可视化页使用)。

数据来源(arq 0.28 真实 Redis 结构):
- 队列本身是 Redis ZSET(key = queue_name):score = 入队毫秒时间戳,
  因此 ZCARD = 待执行任务总数、ZCOUNT 按 score 分「已到点/延迟」;
- 每个 worker 进程写自己的节点级健康 key
  (key = {queue_name}:workers:{hostname}:{pid},TTL = interval + 1 秒),值形如
  "Sep-08 12:00:00 j_complete=3 j_failed=0 j_retried=0 j_ongoing=1 queued=2";
  worker 退出时 arq 自动删除自己的 key,失联(kill -9)节点 key 在 TTL 内自然过期 →
  SCAN {queue_name}:workers:* 即可枚举全部存活/失联节点(替代旧版共享 key,
  后者后写覆盖无法区分节点);
- 正在执行的任务近似数 = SCAN arq:in-progress:* 的 key 数量(worker 执行任务时登记)。

注意:连接的是 arq 专属 Redis db=1(common_arq.queue 里的生产端连接),
与 system 服务常规的业务 Redis(db0)相互独立。
"""
from datetime import datetime
from typing import Optional

from arq.constants import in_progress_key_prefix

from common.common_arq.queue import (
    DEFAULT_QUEUE_NAME, ARQ_REDIS_DSN,
)
from common.common_log.log_init import log
from common.common_redis.redis import client as redis_client


# 节点级健康 key 前缀(与 common_common_arq.queue.worker_health_key 构造一致):
# key = {queue_name}:workers:{hostname}:{pid};SCAN 此前缀即得全部 worker 节点
WORKERS_KEY_PREFIX = ":workers:"

# 心跳新鲜判定窗口(秒):worker 每 10s 写一次(key TTL=11s),TTL>0 即视为在线;
# 该常量作为秒级统计的展示参考与兜底判定(3 倍间隔内未见更新视为失联)
HEALTH_FRESH_SECONDS = 30


def _workers_key_prefix(queue_name: str) -> str:
    return f"{queue_name}{WORKERS_KEY_PREFIX}"


def _parse_health_value(raw: str) -> Optional[dict]:
    """解析健康 key 的值,如 "Sep-08 12:00:00 j_complete=3 j_failed=0 queued=2"。

    :return: {"heartbeat": datetime, "stats": {j_complete: int, ...}};解析失败返回 None
    """
    parts = raw.split()
    if len(parts) < 2:
        return None
    try:
        # 值里没有年份:用当前年份补齐,跨年边界(12-31 → 01-01)回退一年
        heartbeat = datetime.strptime(f"{parts[0]} {parts[1]}", "%b-%d %H:%M:%S")
        heartbeat = heartbeat.replace(year=datetime.now().year)
        if heartbeat > datetime.now():
            heartbeat = heartbeat.replace(year=heartbeat.year - 1)
    except ValueError:
        return None
    stats = {}
    for p in parts[2:]:
        if "=" in p:
            key, _, val = p.partition("=")
            try:
                stats[key] = int(val)
            except ValueError:
                stats[key] = None
    return {"heartbeat": heartbeat, "stats": stats}


class ArqMonitorService:
    """arq 队列/worker 监控查询(全部只读,不影响队列)。"""

    @staticmethod
    async def queue_metrics(queue_name: str = DEFAULT_QUEUE_NAME) -> dict:
        """队列指标:深度 / 已到点 / 延迟 / 执行中(近似)/ 健康是否存活。"""
        try:
            depth = await redis_client.client.zcard(queue_name)
            now_ms = int(datetime.now().timestamp() * 1000)
            # score <= now:到点可执行;score > now:延迟任务(defer_until)
            due = await redis_client.client.zcount(queue_name, "-inf", now_ms)
            delayed = max(0, depth - due)
            # 正在执行的任务数(worker 执行时登记 in-progress key,结束即删)
            in_progress = 0
            async for _ in redis_client.client.scan_iter(match=f"{in_progress_key_prefix}*", count=500):
                in_progress += 1
            # 节点级健康 key 枚举:存在未过期 key 即代表有 worker 在线
            worker_nodes = await ArqMonitorService._scan_worker_nodes(queue_name)
            alive_nodes = [n for n in worker_nodes if n["alive"]]
            alive = bool(alive_nodes)
            # 在线节点中最近一次心跳的节点
            latest = min(alive_nodes, key=lambda n: n["secondsSinceHeartbeat"] or 0) if alive_nodes else None
        except Exception as e:  # noqa: BLE001  Redis 不可达等
            log.error("arq queue metrics failed: {}", e)
            return {
                "queueName": queue_name,
                "redis": ARQ_REDIS_DSN,
                "depth": -1, "due": -1, "delayed": -1, "inProgress": -1,
                "healthy": False, "error": str(e),
            }
        return {
            "queueName": queue_name,
            "redis": ARQ_REDIS_DSN,
            # 队列深度:全部待执行任务(到点 + 延迟)
            "depth": depth,
            # 已到点待执行(worker 空闲即可消费)
            "due": due,
            # 延迟任务(还不到执行时间)
            "delayed": delayed,
            # 正在执行的任务数(近似值,来自 in-progress key 扫描)
            "inProgress": in_progress,
            # 是否有健康的 worker 在线(至少一个节点 key 新鲜)
            "healthy": alive,
            # 最近一次心跳时间(无 worker 时为 None)
            "heartbeat": latest["heartbeat"] if latest else None,
            # 在线 worker 节点数(分布式扩展后 >1)
            "workerCount": len(alive_nodes),
        }

    @staticmethod
    async def _scan_worker_nodes(queue_name: str) -> list[dict]:
        """枚举节点级健康 key:返回每个 worker 节点的心跳/统计/TTL(全只读)。

        :return: 节点列表(alive=False 表示已失联但 key 尚未过期清除);Redis 不可达时返回 []
        """
        prefix = _workers_key_prefix(queue_name)
        nodes = []
        try:
            async for key in redis_client.client.scan_iter(match=f"{prefix}*", count=500):
                raw = await redis_client.client.get(key)
                ttl = await redis_client.client.ttl(key)
                if not raw:
                    continue
                parsed = _parse_health_value(raw)
                alive = ttl is not None and ttl > 0
                stats = (parsed or {}).get("stats") or {}
                nodes.append({
                    "workerId": key[len(prefix):],
                    "alive": alive,
                    "heartbeat": parsed["heartbeat"].strftime("%Y-%m-%d %H:%M:%S") if parsed else None,
                    # 距最近心跳的秒数(越短说明该节点越活跃)
                    "secondsSinceHeartbeat": int((datetime.now() - parsed["heartbeat"]).total_seconds()) if parsed else None,
                    "healthKeyTtl": ttl,
                    "raw": raw,
                    # j_* 均为该 worker 进程内累计,重启清零
                    "complete": stats.get("j_complete"),
                    "failed": stats.get("j_failed"),
                    "retried": stats.get("j_retried"),
                    "ongoing": stats.get("j_ongoing"),
                    "queued": stats.get("queued"),
                })
        except Exception as e:  # noqa: BLE001  Redis 不可达等
            log.error("scan worker nodes failed: {}", e)
            return []
        return nodes

    @staticmethod
    async def worker_health(queue_name: str = DEFAULT_QUEUE_NAME) -> dict:
        """worker 节点健康看板:节点清单 + 聚合统计(全部只读)。

        节点级语义:每个 worker 进程一个健康 key(hostname:pid),SCAN 前缀枚举;
        alive = key 未过期(最近一次心跳在 TTL 窗口内)。聚合口径:
        - count/total:在线节点数 / 发现节点总数;
        - j_* 统计:全节点求和(queued 例外——它是整队列深度,各 worker 看到相同值,
          求和会重复计数,故取在线最新节点的值)。
        """
        nodes = await ArqMonitorService._scan_worker_nodes(queue_name)
        alive_nodes = [n for n in nodes if n["alive"]]
        # 在线节点中最近一次心跳的节点(alive 列表非空时必有值)
        latest = min(alive_nodes, key=lambda n: n["secondsSinceHeartbeat"] or 0) if alive_nodes else None

        def _sum(stats_key: str):
            vals = [n[stats_key] for n in alive_nodes if isinstance(n[stats_key], int)]
            return sum(vals) if vals else None

        if not nodes:
            return {
                "queueName": queue_name,
                "alive": False,
                "count": 0, "total": 0, "workers": [],
                "reason": "未发现 worker 节点(worker 未启动或已全部退出)",
                "heartbeat": None, "secondsSinceHeartbeat": None, "healthKeyTtl": None,
                "complete": None, "failed": None, "retried": None, "ongoing": None, "queued": None,
            }
        return {
            "queueName": queue_name,
            "alive": bool(alive_nodes),
            # 在线节点数 / 发现节点总数(含 key 未过期的失联节点)
            "count": len(alive_nodes),
            "total": len(nodes),
            # 节点清单(workerId = hostname:pid,分布式扩展后可枚举所有 worker)
            "workers": nodes,
            # 以下为聚合口径(见 docstring)
            "heartbeat": latest["heartbeat"] if latest else None,
            "secondsSinceHeartbeat": latest["secondsSinceHeartbeat"] if latest else None,
            "healthKeyTtl": latest["healthKeyTtl"] if latest else None,
            "complete": _sum("complete"),
            "failed": _sum("failed"),
            "retried": _sum("retried"),
            "ongoing": _sum("ongoing"),
            "queued": latest["queued"] if latest else None,
            "reason": None if alive_nodes else "未发现新鲜心跳(worker 未启动或长时间失联)",
        }