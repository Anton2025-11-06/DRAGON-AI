# -*- coding: utf-8 -*-
"""arq 任务队列监控路由:分片队列总览 + 切片数配置 + 单队列指标/worker 健康。

对应前端「系统管理 → ARQ 任务监控」菜单页的数据源:
- GET /arq/overview       分片队列总览(切片数 + 每队列指标/worker 列表,页面主数据源)
- GET /arq/split-config   当前切片数配置(workflow_queue:split_number)
- PUT /arq/split-config   修改切片数并写入 arq Redis(返回新值)
- GET /arq/queue-metrics  单队列指标(兼容旧版,队列缺省 split_1)
- GET /arq/worker-health  单队列 worker 节点清单 + 聚合(兼容旧版,队列缺省 split_1)
"""
from fastapi import APIRouter, Body, Query, Request

from common.common_arq.queue import QUEUE_NAME, SPLIT_NAME
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.services.arq_monitor_service import ArqMonitorService

router = APIRouter(prefix="/arq", tags=["ARQ任务监控"])

# 缺省队列:切片 1(无配置时默认单切片)
_DEFAULT_QUEUE = f"{QUEUE_NAME}:{SPLIT_NAME}1"


@router.get("/overview", summary="arq 分片队列总览(页面主数据源)")
@has_permission("system:arq:list")
async def overview(request: Request):
    return ApiResponse.success(data=await ArqMonitorService.overview())


@router.get("/split-config", summary="读取切片数配置")
@has_permission("system:arq:list")
async def get_split_config(request: Request):
    return ApiResponse.success(data=await ArqMonitorService.split_config())


@router.put("/split-config", summary="修改切片数配置")
@has_permission("system:arq:list")
async def put_split_config(request: Request,
                           splitNumber: int = Body(..., embed=True, ge=1, le=32,
                                                    description="切片数")):
    return ApiResponse.success(data=await ArqMonitorService.update_split_number(splitNumber))


@router.get("/queue-metrics", summary="arq 队列指标(单队列)")
@has_permission("system:arq:list")
async def queue_metrics(request: Request, queue: str = Query(None, description="队列名,缺省取切片 1")):
    return ApiResponse.success(data=await ArqMonitorService.queue_metrics(queue or _DEFAULT_QUEUE))


@router.get("/worker-health", summary="arq worker 节点健康(列表+聚合)")
@has_permission("system:arq:list")
async def worker_health(request: Request, queue: str = Query(None, description="队列名,缺省取切片 1")):
    return ApiResponse.success(data=await ArqMonitorService.worker_health(queue or _DEFAULT_QUEUE))