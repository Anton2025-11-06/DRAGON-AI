# -*- coding: utf-8 -*-
"""arq 任务队列监控路由:分片队列总览 + 切片数配置 + 单队列 worker 健康。

对应前端「系统管理 → ARQ 任务监控」菜单页的数据源(每个接口都带 pipeline 参数,
页面靠它在工作流执行队列与知识库解析队列之间切换):
- GET /arq/pipelines      可监控的流水线清单(下拉选项,来自 common_arq 的注册表)
- GET /arq/overview       指定流水线的分片队列总览(在线队列 + worker 列表,页面主数据源)
- GET /arq/split-config   指定流水线的切片数配置({基础队列名}:split_number)
- PUT /arq/split-config   修改指定流水线的切片数并写入其 arq Redis(返回新值)
- GET /arq/worker-health  单队列 worker 节点清单 + 聚合(兼容旧版,页面未使用)

pipeline 取值必须是 common_arq.queue._PIPELINE_CFG 里注册过的键,未知值由 pipeline_cfg
抛 ValueError → 全局 handler 转 400(不静默回落到工作流队列:那会把知识库的 worker
显示成工作流的,监控页最坏的表现就是说谎)。
"""
from fastapi import APIRouter, Body, Query, Request

from common.common_arq.queue import PIPELINE_WORKFLOW, pipelines
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.services.arq_monitor_service import ArqMonitorService

router = APIRouter(prefix="/arq", tags=["ARQ任务监控"])

# pipeline 参数的接口说明(三个接口口径一致,写一处免得各说各话)
_PIPELINE_DESC = "arq 流水线:workflow(工作流执行)|ragflow(知识库解析)|graphflow(图谱构建)"


@router.get("/pipelines", summary="可监控的 arq 流水线清单(页面下拉)")
@has_permission("system:arq:list")
async def pipeline_list(request: Request):
    return ApiResponse.success(data=pipelines())


@router.get("/overview", summary="arq 分片队列总览(页面主数据源)")
@has_permission("system:arq:list")
async def overview(request: Request,
                   pipeline: str = Query(PIPELINE_WORKFLOW, description=_PIPELINE_DESC)):
    return ApiResponse.success(data=await ArqMonitorService.overview(pipeline))


@router.get("/split-config", summary="读取切片数配置")
@has_permission("system:arq:list")
async def get_split_config(request: Request,
                           pipeline: str = Query(PIPELINE_WORKFLOW, description=_PIPELINE_DESC)):
    return ApiResponse.success(data=await ArqMonitorService.split_config(pipeline))


@router.put("/split-config", summary="修改切片数配置")
@has_permission("system:arq:list")
async def put_split_config(request: Request,
                           pipeline: str = Query(PIPELINE_WORKFLOW, description=_PIPELINE_DESC),
                           splitNumber: int = Body(..., embed=True, ge=1, le=32,
                                                    description="切片数")):
    return ApiResponse.success(
        data=await ArqMonitorService.update_split_number(splitNumber, pipeline))


@router.get("/worker-health", summary="arq worker 节点健康(列表+聚合)")
@has_permission("system:arq:list")
async def worker_health(request: Request,
                        queue: str = Query(..., description="队列名,如 workflow_queue:split_1"),
                        pipeline: str = Query(PIPELINE_WORKFLOW, description=_PIPELINE_DESC)):
    return ApiResponse.success(data=await ArqMonitorService.worker_health(queue, pipeline))
