from fastapi import APIRouter, HTTPException, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_system.schemas.user_schema import (
    AssignRolesRequest, ChangeOwnPasswordRequest, ResetPasswordRequest,
    UserCreateRequest, UserUpdateRequest, VerifyRequest)
from service.service_system.services.user_service import UserService
from service.service_system.services.rbac_service import RbacService

router = APIRouter(prefix="/users", tags=["用户管理"])


@router.post("", summary="创建用户")
@has_permission("system:user:add")
async def create_user(request: Request, body: UserCreateRequest):
    result = await UserService.create_user(body)
    if result:
        return ApiResponse.success("创建成功")
    else:
        return ApiResponse.error(500, "创建失败")


@router.post("/verify", summary="校验用户密码")
async def verify(request: VerifyRequest):
    result = await UserService.verify_password(request.password, request.user_id)
    return ApiResponse.success(result)


@router.get("/me/info", summary="当前登录用户信息(含角色/权限/菜单)")
async def me(request: Request):
    result = await UserService.me(request)
    return ApiResponse.success(data=result)


@router.get("/me/menus", summary="当前用户可见菜单树（前端导航渲染）")
async def my_menus(request: Request):
    login_user = await get_login_user(request)
    return ApiResponse.success(data=await RbacService.user_menu_tree(login_user["user_id"]))


@router.get("/{user_id}/roles", summary="查询用户角色")
async def user_roles(user_id: int):
    result = await RbacService.user_roles(user_id)
    return ApiResponse.success(data=result)


@router.put("/{user_id}/roles", summary="分配用户角色")
@has_permission("system:user:assign")
async def assign_roles(request: Request, user_id: int, body: AssignRolesRequest):
    await RbacService.assign_roles(user_id, body.role_ids)
    return ApiResponse.success(message="分配成功")


@router.put("/{user_id}/password", summary="管理员重置密码")
@has_permission("system:user:reset")
async def reset_password(request: Request, user_id: int, body: ResetPasswordRequest):
    await UserService.reset_password(user_id, body)
    return ApiResponse.success(message="密码重置成功")


@router.put("/{user_id}/own-password", summary="修改自己的密码")
async def change_own_password(user_id: int, request: ChangeOwnPasswordRequest, req: Request):
    # 仅允许修改本人密码，且需校验原密码
    login_user = await get_login_user(req)
    if login_user["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="禁止修改他人密码")
    await UserService.change_own_password(req, request.old_password, request.new_password)
    return ApiResponse.success(message="密码修改成功")


@router.get("/{user_id}", summary="获取用户详情")
async def get_user(user_id: int):
    user = await UserService.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = await RbacService.user_roles(user_id)
    return ApiResponse.success(data={
        "user_id": user.user_id, "username": user.username, "real_name": user.real_name,
        "email": user.email, "phone": user.phone, "dept_id": user.dept_id or 0,
        "status": user.status, "roles": roles,
    })


@router.get("", summary="查询用户列表（按数据权限过滤）")
@has_permission("system:user:list")
async def list_users(
        request: Request,
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(10, ge=1, le=100, description="每页数量"),
        username: str = Query(None, description="用户名模糊查询"),
        status: int = Query(None, ge=0, le=1, description="状态")):
    result = await UserService.list_users(request=request, page=page, page_size=page_size,
                                          username=username, status=status)
    return ApiResponse.success(data=result)


@router.put("/{user_id}", summary="更新用户")
@has_permission("system:user:edit")
async def update_user(request: Request, user_id: int, body: UserUpdateRequest):
    await UserService.update_user(user_id, body)
    return ApiResponse.success(message="更新成功")


@router.delete("/{user_id}", summary="删除用户")
@has_permission("system:user:delete")
async def delete_user(request: Request, user_id: int):
    await UserService.delete_user(user_id)
    return ApiResponse.success(message="删除成功")