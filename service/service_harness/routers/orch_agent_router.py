# -*- coding: utf-8 -*-
"""单智能体编排路由（/orch/agent/*）。

两层权限（口径见计划 §1）：
- RBAC 只管「全局入口 + 页面准入」：create→agent:orch:single:add、
  page/usable-resources→agent:orch:single:list；授权入口走通用 /acl/*（RBAC grant）；
- 行级按钮（查看/编辑/删除/验证）全由 ACL 在 service 单点判定（decorate_actions 下发显隐），
  故 detail/modify/delete 不挂 @has_permission，交由 ensure_action*把关。
"""
from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_harness.schemas.orch_schema import OrchAgentSaveReq
from service.service_harness.services.orch_agent_service import OrchAgentService

router = APIRouter(prefix="/orch/agent", tags=["智能体编排-单智能体"])


@router.get("/page", summary="分页查询单智能体（搜索名称/用途）")
@has_permission("agent:orch:single:list")
async def page_agents(request: Request, page: int = 1, page_size: int = 10,
                      keyword: str = None, status: int = None):
    data = await OrchAgentService.page(await get_login_user(request), page, page_size,
                                       keyword, status)
    return ApiResponse.success(data=data)


@router.get("/usable-resources/{kind}", summary="挂载候选（use 口径，仅公开信息）")
@has_permission("agent:orch:single:list")
async def usable_resources(request: Request, kind: str):
    """技能/工具/MCP/知识库候选：id/name/description，不含密钥（克隆与 picker 同源）。"""
    try:
        data = await OrchAgentService.usable_resources(await get_login_user(request), kind)
        return ApiResponse.success(data={"items": data, "total": len(data)})
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/{agent_id}/detail", summary="单智能体详情（ACL view）")
async def agent_detail(request: Request, agent_id: int):
    detail = await OrchAgentService.detail(await get_login_user(request), agent_id)
    if detail is None:
        return ApiResponse.error(400, "智能体不存在")
    return ApiResponse.success(data=detail)


@router.post("/create", summary="新增单智能体（RBAC add）")
@has_permission("agent:orch:single:add")
async def create_agent(request: Request, body: OrchAgentSaveReq):
    try:
        login_user = await get_login_user(request)
        new_id = await OrchAgentService.create(
            login_user, body.name, body.purpose, body.systemPrompt,
            body.mounts.model_dump() if body.mounts else None,
            True if body.status is None else bool(body.status))
        return ApiResponse.success(data={"id": new_id}, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{agent_id}/modify", summary="编辑单智能体（ACL edit）")
async def modify_agent(request: Request, agent_id: int, body: OrchAgentSaveReq):
    try:
        login_user = await get_login_user(request)
        await OrchAgentService.update(
            login_user, agent_id, name=body.name, purpose=body.purpose,
            system_prompt=body.systemPrompt,
            mounts=body.mounts.model_dump() if body.mounts is not None else None,
            status=body.status)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{agent_id}", summary="删除单智能体（ACL 严格 delete，软删）")
async def delete_agent(request: Request, agent_id: int):
    try:
        ok = await OrchAgentService.delete(await get_login_user(request), agent_id)
        if not ok:
            return ApiResponse.error(400, "智能体不存在或已删除")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
