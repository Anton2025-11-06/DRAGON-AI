# -*- coding: utf-8 -*-
"""目标模式编排路由（/orch/goal/*）。

两层权限（口径见计划 §1）：RBAC page/usable-resources/clone-sources→agent:orch:goal:list、
create→agent:orch:goal:add；行级按钮全由 ACL 在 service 单点判定，detail/modify/delete 不挂
@has_permission；授权走通用 /acl/*（RBAC goal:grant + ACL share 两层 AND）。
"""
from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_harness.schemas.orch_schema import OrchGoalSaveReq
from service.service_harness.services.orch_agent_service import OrchAgentService
from service.service_harness.services.orch_goal_service import OrchGoalService

router = APIRouter(prefix="/orch/goal", tags=["智能体编排-目标模式"])


@router.get("/page", summary="分页查询目标（搜索名称/用途）")
@has_permission("agent:orch:goal:list")
async def page_goals(request: Request, page: int = 1, page_size: int = 10,
                     keyword: str = None, status: int = None):
    data = await OrchGoalService.page(await get_login_user(request), page, page_size,
                                      keyword, status)
    return ApiResponse.success(data=data)


@router.get("/usable-resources/{kind}", summary="成员挂载候选（use 口径，仅公开信息）")
@has_permission("agent:orch:goal:list")
async def usable_resources(request: Request, kind: str):
    try:
        data = await OrchAgentService.usable_resources(await get_login_user(request), kind)
        return ApiResponse.success(data={"items": data, "total": len(data)})
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/clone-sources", summary="克隆来源候选（当前用户可见的单智能体）")
@has_permission("agent:orch:goal:list")
async def clone_sources(request: Request):
    data = await OrchGoalService.clone_sources(await get_login_user(request))
    return ApiResponse.success(data={"items": data, "total": len(data)})


@router.get("/clone-source/{agent_id}", summary="克隆预填：单智能体详情（ACL view）")
async def clone_source_detail(request: Request, agent_id: int):
    detail = await OrchAgentService.detail(await get_login_user(request), agent_id)
    if detail is None:
        return ApiResponse.error(400, "智能体不存在")
    return ApiResponse.success(data=detail)


@router.get("/{goal_id}/detail", summary="目标详情（ACL view）")
async def goal_detail(request: Request, goal_id: int):
    detail = await OrchGoalService.detail(await get_login_user(request), goal_id)
    if detail is None:
        return ApiResponse.error(400, "目标不存在")
    return ApiResponse.success(data=detail)


@router.post("/create", summary="新增目标（RBAC add）")
@has_permission("agent:orch:goal:add")
async def create_goal(request: Request, body: OrchGoalSaveReq):
    try:
        new_id = await OrchGoalService.create(await get_login_user(request), body)
        return ApiResponse.success(data={"id": new_id}, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{goal_id}/modify", summary="编辑目标（ACL edit）")
async def modify_goal(request: Request, goal_id: int, body: OrchGoalSaveReq):
    try:
        await OrchGoalService.update(await get_login_user(request), goal_id, body)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{goal_id}", summary="删除目标（ACL 严格 delete，软删）")
async def delete_goal(request: Request, goal_id: int):
    try:
        ok = await OrchGoalService.delete(await get_login_user(request), goal_id)
        if not ok:
            return ApiResponse.error(400, "目标不存在或已删除")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
