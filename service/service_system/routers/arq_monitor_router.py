# -*- coding: utf-8 -*-
"""arq 任务队列监控路由:队列指标 + worker 节点健康。

对应前端「系统管理 → ARQ 任务监控」菜单页的数据源:
- GET /arq/queue-metrics:队列深度/待执行/延迟/执行中/健康状态(卡片式指标)
- GET /arq/worker-health:worker 节点清单 + 聚合统计(节点级健康看板,
  workers 数组枚举每个存活的 worker(hostname:pid),分布式扩展后可直接展示)
"""
from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.services.arq_monitor_service import ArqMonitorService

router = APIRouter(prefix="/arq", tags=["ARQ任务监控"])


@router.get("/queue-metrics", summary="arq 队列指标")
@has_permission("system:arq:list")
async def queue_metrics(request: Request, queue: str = Query(None, description="队列名,缺省取默认队列")):
    from common.common_arq.queue import DEFAULT_QUEUE_NAME
    return ApiResponse.success(data=await ArqMonitorService.queue_metrics(queue or DEFAULT_QUEUE_NAME))


@router.get("/worker-health", summary="arq worker 节点健康(列表+聚合)")
@has_permission("system:arq:list")
async def worker_health(request: Request, queue: str = Query(None, description="队列名,缺省取默认队列")):
    from common.common_arq.queue import DEFAULT_QUEUE_NAME
    return ApiResponse.success(data=await ArqMonitorService.worker_health(queue or DEFAULT_QUEUE_NAME))