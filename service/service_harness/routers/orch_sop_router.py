# -*- coding: utf-8 -*-
"""SOP 模式编排路由（/orch/sop/*）。

两层权限（口径见计划 §1）：RBAC page/usable-resources/clone-sources→agent:orch:sop:list、
create→agent:orch:sop:add；行级按钮全由 ACL 在 service 单点判定，detail/modify/delete 不挂
@has_permission；授权走通用 /acl/*（RBAC sop:grant + ACL share 两层 AND）。
"""
from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_harness.schemas.orch_schema import OrchSopSaveReq
from service.service_harness.services.orch_agent_service import OrchAgentService
from service.service_harness.services.orch_sop_service import OrchSopService

router = APIRouter(prefix="/orch/sop", tags=["智能体编排-SOP模式"])


@router.get("/page", summary="分页查询 SOP（搜索名称/用途）")
@has_permission("agent:orch:sop:list")
async def page_sops(request: Request, page: int = 1, page_size: int = 10,
                    keyword: str = None, status: int = None):
    data = await OrchSopService.page(await get_login_user(request), page, page_size,
                                     keyword, status)
    return ApiResponse.success(data=data)


@router.get("/usable-resources/{kind}", summary="节点挂载候选（use 口径，仅公开信息）")
@has_permission("agent:orch:sop:list")
async def usable_resources(request: Request, kind: str):
    try:
        data = await OrchAgentService.usable_resources(await get_login_user(request), kind)
        return ApiResponse.success(data={"items": data, "total": len(data)})
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/clone-sources", summary="克隆来源候选（当前用户可见的单智能体）")
@has_permission("agent:orch:sop:list")
async def clone_sources(request: Request):
    data = await OrchSopService.clone_sources(await get_login_user(request))
    return ApiResponse.success(data={"items": data, "total": len(data)})


@router.get("/clone-source/{agent_id}", summary="克隆预填：单智能体详情（ACL view）")
async def clone_source_detail(request: Request, agent_id: int):
    detail = await OrchAgentService.detail(await get_login_user(request), agent_id)
    if detail is None:
        return ApiResponse.error(400, "智能体不存在")
    return ApiResponse.success(data=detail)


@router.get("/{sop_id}/detail", summary="SOP 详情（ACL view）")
async def sop_detail(request: Request, sop_id: int):
    detail = await OrchSopService.detail(await get_login_user(request), sop_id)
    if detail is None:
        return ApiResponse.error(400, "SOP 不存在")
    return ApiResponse.success(data=detail)


@router.post("/create", summary="新增 SOP（RBAC add）")
@has_permission("agent:orch:sop:add")
async def create_sop(request: Request, body: OrchSopSaveReq):
    try:
        new_id = await OrchSopService.create(await get_login_user(request), body)
        return ApiResponse.success(data={"id": new_id}, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{sop_id}/modify", summary="编辑 SOP（ACL edit）")
async def modify_sop(request: Request, sop_id: int, body: OrchSopSaveReq):
    try:
        await OrchSopService.update(await get_login_user(request), sop_id, body)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{sop_id}", summary="删除 SOP（ACL 严格 delete，软删）")
async def delete_sop(request: Request, sop_id: int):
    try:
        ok = await OrchSopService.delete(await get_login_user(request), sop_id)
        if not ok:
            return ApiResponse.error(400, "SOP 不存在或已删除")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
