# -*- coding: utf-8 -*-
"""团队模式编排路由（/orch/team/*）。

两层权限（口径见计划 §1、orch_agent_router 顶部）：
- RBAC：page/usable-resources→agent:orch:team:list、create→agent:orch:team:add；
  授权入口走通用 /acl/*（RBAC agent:orch:team:grant + ACL share 两层 AND）；
- 行级按钮（查看/编辑/删除/验证）全由 ACL 在 service 单点判定，故 detail/modify/
  delete 不挂 @has_permission。
"""
from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_harness.schemas.orch_schema import OrchTeamSaveReq
from service.service_harness.services.orch_agent_service import OrchAgentService
from service.service_harness.services.orch_team_service import OrchTeamService

router = APIRouter(prefix="/orch/team", tags=["智能体编排-团队模式"])


@router.get("/page", summary="分页查询团队（名称/用途分别检索）")
@has_permission("agent:orch:team:list")
async def page_teams(request: Request, page: int = 1, page_size: int = 10,
                     name: str = None, purpose: str = None, status: int = None):
    data = await OrchTeamService.page(await get_login_user(request), page, page_size,
                                      name, purpose, status)
    return ApiResponse.success(data=data)


@router.get("/usable-resources/{kind}", summary="成员挂载候选（use 口径，仅公开信息）")
@has_permission("agent:orch:team:list")
async def usable_resources(request: Request, kind: str):
    try:
        data = await OrchAgentService.usable_resources(await get_login_user(request), kind)
        return ApiResponse.success(data={"items": data, "total": len(data)})
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/clone-sources", summary="克隆来源候选（当前用户可见的单智能体）")
@has_permission("agent:orch:team:list")
async def clone_sources(request: Request):
    data = await OrchTeamService.clone_sources(await get_login_user(request))
    return ApiResponse.success(data={"items": data, "total": len(data)})


@router.get("/clone-source/{agent_id}", summary="克隆预填：单智能体详情（ACL view）")
async def clone_source_detail(request: Request, agent_id: int):
    detail = await OrchAgentService.detail(await get_login_user(request), agent_id)
    if detail is None:
        return ApiResponse.error(400, "智能体不存在")
    return ApiResponse.success(data=detail)


@router.get("/{team_id}/detail", summary="团队详情（ACL view）")
async def team_detail(request: Request, team_id: int):
    detail = await OrchTeamService.detail(await get_login_user(request), team_id)
    if detail is None:
        return ApiResponse.error(400, "团队不存在")
    return ApiResponse.success(data=detail)


@router.post("/create", summary="新增团队（RBAC add）")
@has_permission("agent:orch:team:add")
async def create_team(request: Request, body: OrchTeamSaveReq):
    try:
        new_id = await OrchTeamService.create(await get_login_user(request), body)
        return ApiResponse.success(data={"id": new_id}, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{team_id}/modify", summary="编辑团队（ACL edit）")
async def modify_team(request: Request, team_id: int, body: OrchTeamSaveReq):
    try:
        await OrchTeamService.update(await get_login_user(request), team_id, body)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{team_id}", summary="删除团队（ACL 严格 delete，软删）")
async def delete_team(request: Request, team_id: int):
    try:
        ok = await OrchTeamService.delete(await get_login_user(request), team_id)
        if not ok:
            return ApiResponse.error(400, "团队不存在或已删除")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
