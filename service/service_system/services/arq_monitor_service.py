# -*- coding: utf-8 -*-
"""arq 分片队列监控:队列指标 + worker 节点健康 + 切片数配置(供 system 监控页使用)。

数据来源(arq 0.28 真实 Redis 结构):
- 队列是 Redis ZSET(key = workflow_queue:split_{N}):score = 入队毫秒时间戳,
  ZCARD = 待执行任务总数、ZCOUNT 按 score 分「已到点/延迟」;
- 每个 worker 进程写自己的健康 key
  (key = workflow_worker:{queue_name}:{hostname}:{pid},TTL = interval + 1 秒),值形如
  "Sep-08 12:00:00 j_complete=3 j_failed=0 j_retried=0 j_ongoing=1 queued=2";
  worker 退出时 arq 自动删除自己的 key,失联(kill -9)节点 key 在 TTL 内自然过期 →
  SCAN f"workflow_worker:{queue_name}:" 即可枚举该队列全部节点;
- 正在执行的任务近似数 = SCAN arq:in-progress:* 的 key 数量(worker 执行任务时登记)。

连接说明:本服务直连 arq 专属 Redis(common_arq.queue 的 ARQ_REDIS_URL),
与 system 服务常规的业务 Redis(common_redis.client)相互独立,两者不得混用。
"""
from datetime import datetime
from typing import Optional

from arq.constants import in_progress_key_prefix

from common.common_arq.queue import (
    ARQ_REDIS_URL, DEFAULT_WORKER_NAME, MAX_SPLIT_NUMBER, MIN_SPLIT_NUMBER,
    QUEUE_NAME, get_arq_redis, get_split_number, set_split_number,
)
from common.common_log.log_init import log


# 心跳新鲜判定窗口(秒):worker 每 10s 写一次(key TTL=11s),3 倍间隔未见更新视为失联
HEALTH_FRESH_SECONDS = 30


def _workers_key_prefix(queue_name: str) -> str:
    """该队列所有 worker 健康 key 的 SCAN 前缀(格式见模块 docstring)。"""
    return f"{DEFAULT_WORKER_NAME}:{queue_name}:"


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
    """arq 队列/worker 监控与切片数配置(读取/修改均直连 arq 专属 Redis)。"""

    # ==================== 切片数配置(监控页修改功能) ====================

    @staticmethod
    async def split_config() -> dict:
        """当前切片数配置(workflow_queue:split_number 的值,不存在按默认 1)。"""
        return {"splitNumber": await get_split_number()}

    @staticmethod
    async def update_split_number(n: int) -> dict:
        """修改切片数并写入 arq Redis(越界抛 ValueError → 全局 handler 转 400)。"""
        if not MIN_SPLIT_NUMBER <= n <= MAX_SPLIT_NUMBER:
            raise ValueError(f"切片数必须在 {MIN_SPLIT_NUMBER}~{MAX_SPLIT_NUMBER} 之间, 实际 {n}")
        return {"splitNumber": await set_split_number(n)}

    # ==================== 队列指标(单队列,兼容旧接口) ====================

    @staticmethod
    async def queue_metrics(queue_name: str) -> dict:
        """队列指标:深度 / 已到点 / 延迟 / 执行中(近似)/ 是否存活。"""
        arq = await get_arq_redis()
        try:
            depth = await arq.zcard(queue_name)
            now_ms = int(datetime.now().timestamp() * 1000)
            # score <= now:到点可执行;score > now:延迟任务(defer_until)
            due = await arq.zcount(queue_name, "-inf", now_ms)
            delayed = max(0, depth - due)
            # 正在执行的任务数(worker 执行时登记 in-progress key,结束即删)
            in_progress = 0
            async for _ in arq.scan_iter(match=f"{in_progress_key_prefix}*", count=500):
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
                "redis": ARQ_REDIS_URL,
                "depth": -1, "due": -1, "delayed": -1, "inProgress": -1,
                "healthy": False, "error": str(e),
            }
        return {
            "queueName": queue_name,
            "redis": ARQ_REDIS_URL,
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

    # ==================== worker 节点健康(按队列) ====================

    @staticmethod
    async def _scan_worker_nodes(queue_name: str) -> list[dict]:
        """枚举某队列的节点级健康 key:每个 worker 一份心跳/统计/TTL(全只读)。

        :return: 节点列表(alive=False 表示已失联但 key 尚未过期清除);Redis 不可达时返回 []
        """
        prefix = _workers_key_prefix(queue_name)
        arq = await get_arq_redis()
        nodes = []
        try:
            async for key in arq.scan_iter(match=f"{prefix}*", count=500):
                # arq Redis 池 decode_responses=False:key/value 均为 bytes,统一解码
                key = key.decode("utf-8") if isinstance(key, bytes) else key
                raw = await arq.get(key)
                ttl = await arq.ttl(key)
                if not raw:
                    continue
                raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
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
    async def worker_health(queue_name: str) -> dict:
        """worker 节点健康看板:节点清单 + 聚合统计(全部只读)。

        节点级语义:每个 worker 进程一个健康 key(queue_name+hostname:pid),SCAN 前缀枚举;
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

    # ==================== 分片队列总览(监控页主数据源) ====================

    @staticmethod
    async def overview() -> dict:
        """分片队列总览:SCAN 健康 key 直接枚举在线的队列与 worker(不读切片数配置)。

        每个健康 key = workflow_worker:{queue_name}:{hostname}:{pid},值 = 心跳+消费统计;
        key 存在(TTL>0)即在线。按队列分组返回,页面按队列折叠展示。
        """
        arq = await get_arq_redis()
        groups: dict[str, list[dict]] = {}
        try:
            async for key in arq.scan_iter(
                match=f"{DEFAULT_WORKER_NAME}:{QUEUE_NAME}:*", count=500
            ):
                # arq Redis 池 decode_responses=False:key/value 均为 bytes,统一解码
                key = key.decode("utf-8") if isinstance(key, bytes) else key
                ttl = await arq.ttl(key)
                if ttl is None or ttl <= 0:
                    continue  # 已过期/不存在 → 非在线节点,跳过
                raw = await arq.get(key)
                raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not raw:
                    continue
                parsed = _parse_health_value(raw)
                stats = (parsed or {}).get("stats") or {}
                # key 形如 workflow_worker:workflow_queue:split_{N}:{host}:{pid}
                # (hostname 不含冒号),中间两段即队列名,剩余为 workerId
                parts = key.split(":")
                if len(parts) < 5:
                    continue
                queue_name = ":".join(parts[1:3])
                worker_id = ":".join(parts[3:])
                groups.setdefault(queue_name, []).append({
                    "workerId": worker_id,
                    "alive": True,
                    "heartbeat": parsed["heartbeat"].strftime("%Y-%m-%d %H:%M:%S") if parsed else None,
                    "secondsSinceHeartbeat": int(
                        (datetime.now() - parsed["heartbeat"]).total_seconds()) if parsed else None,
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
            log.error("arq overview scan failed: {}", e)

        queues = [{
            "queueName": qn,
            "workerCount": len(nodes),
            "workers": nodes,
        } for qn, nodes in sorted(groups.items(),
                                 key=lambda i: int(i[0].rsplit("_", 1)[-1]))]
        return {"queues": queues}