"""用户组接口（tb_user_group / tb_user_group_member）。

用户组是资源 ACL 的一类授权主体（grantee_type=4），但本模块只管组织事实：
组名、启停、成员。谁能把资源授给某个组，是 acl_router 的事（share 判定）。
权限点 system:usergroup:list|add|edit|delete|member 见 sql/v2_init.sql PART 8。
"""
from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_system.schemas.usergroup_schema import (
    UserGroupCreateRequest, UserGroupMemberRequest, UserGroupUpdateRequest)
from service.service_system.services.usergroup_service import UserGroupService

router = APIRouter(tags=["用户组"])


# ==================== 下拉与候选（固定段必须声明在 {group_id} 之前） ====================
@router.get("/user-groups/all", summary="启用中的用户组（下拉框）")
async def all_user_groups():
    """授权弹窗的主体候选走 /acl/grantees，这份给「成员维护」等页内下拉用"""
    return ApiResponse.success(data=await UserGroupService.list_all_groups())


@router.get("/user-groups/member-candidates", summary="成员候选用户（按数据范围过滤）")
@has_permission("system:usergroup:list")
async def user_group_member_candidates(
        request: Request,
        keyword: str = Query(None, max_length=64, description="姓名/登录名模糊查询"),
        limit: int = Query(50, ge=1, le=100, description="返回条数")):
    """有成员维护权就能选人，不另外要求 system:user:list（见 service 层注释）"""
    login_user = await get_login_user(request)
    return ApiResponse.success(data=await UserGroupService.member_candidates(
        keyword, limit, login_user=login_user))


# ==================== 组 ====================
@router.get("/user-groups", summary="用户组分页列表")
@has_permission("system:usergroup:list")
async def list_user_groups(request: Request,
                           page: int = Query(1, ge=1, description="页码"),
                           page_size: int = Query(10, ge=1, le=100, description="每页数量"),
                           group_name: str = Query(None, max_length=64, description="名称模糊查询"),
                           status: int = Query(None, ge=0, le=1, description="状态：0-停用 1-启用")):
    login_user = await get_login_user(request)
    return ApiResponse.success(data=await UserGroupService.list_groups(
        page, page_size, group_name, status, login_user=login_user))


@router.post("/user-groups", summary="新增用户组")
@has_permission("system:usergroup:add")
async def create_user_group(request: Request, body: UserGroupCreateRequest):
    login_user = await get_login_user(request)
    await UserGroupService.create_group(body, creator_id=int(login_user.get("user_id") or 0))
    return ApiResponse.success("创建成功")


@router.put("/user-groups/{group_id}", summary="更新用户组")
@has_permission("system:usergroup:edit")
async def update_user_group(request: Request, group_id: int, body: UserGroupUpdateRequest):
    await UserGroupService.update_group(group_id, body)
    return ApiResponse.success("更新成功")


@router.delete("/user-groups/{group_id}", summary="删除用户组（连带撤销指向该组的授权）")
@has_permission("system:usergroup:delete")
async def delete_user_group(request: Request, group_id: int):
    login_user = await get_login_user(request)
    data = await UserGroupService.delete_group(
        group_id, operator_id=int(login_user.get("user_id") or 0))
    return ApiResponse.success(data=data, message="删除成功")


# ==================== 成员 ====================
@router.get("/user-groups/{group_id}/members", summary="用户组成员列表")
@has_permission("system:usergroup:list")
async def list_user_group_members(request: Request, group_id: int):
    return ApiResponse.success(data=await UserGroupService.list_members(group_id))


@router.put("/user-groups/{group_id}/members", summary="重设用户组成员（整表提交）")
@has_permission("system:usergroup:member")
async def assign_user_group_members(request: Request, group_id: int, body: UserGroupMemberRequest):
    """整表语义：body 里就是最终成员集合，空数组即清空

    成员变化不即时作用于已登录用户：group_ids 在登录时算好写进载荷，
    与角色/部门变化同口径（下次登录生效），这里不做在线 token 批量重写。
    """
    login_user = await get_login_user(request)
    data = await UserGroupService.assign_members(
        group_id, body.user_ids, operator_id=int(login_user.get("user_id") or 0))
    return ApiResponse.success(data=data, message="成员已保存")
