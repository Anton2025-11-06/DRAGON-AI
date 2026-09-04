from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from service.service_system.schemas.rbac_schema import (
    DeptCreateRequest, DeptUpdateRequest, MenuCreateRequest, MenuUpdateRequest,
    RoleCreateRequest, RoleMenusRequest, RoleUpdateRequest)
from service.service_system.services.rbac_service import RbacService

router = APIRouter(tags=["权限中心"])


# ==================== 角色 ====================
@router.get("/roles/all", summary="全部启用角色（下拉框）")
async def all_roles():
    return ApiResponse.success(data=await RbacService.list_all_roles())


@router.get("/roles", summary="角色列表")
@has_permission("system:role:list")
async def list_roles(request: Request,
                     page: int = Query(1, ge=1, description="页码"),
                     page_size: int = Query(10, ge=1, le=100, description="每页数量"),
                     role_name: str = Query(None, description="角色名称模糊查询"),
                     status: int = Query(None, ge=0, le=1, description="状态：0-停用，1-启用")):
    return ApiResponse.success(data=await RbacService.list_roles(page, page_size, role_name, status))


@router.post("/roles", summary="创建角色")
@has_permission("system:role:add")
async def create_role(request: Request, body: RoleCreateRequest):
    await RbacService.create_role(body)
    return ApiResponse.success("创建成功")


@router.put("/roles/{role_id}", summary="更新角色")
@has_permission("system:role:edit")
async def update_role(request: Request, role_id: int, body: RoleUpdateRequest):
    await RbacService.update_role(role_id, body)
    return ApiResponse.success("更新成功")


@router.delete("/roles/{role_id}", summary="删除角色")
@has_permission("system:role:delete")
async def delete_role(request: Request, role_id: int):
    await RbacService.delete_role(role_id)
    return ApiResponse.success("删除成功")


@router.put("/roles/{role_id}/menus", summary="分配角色菜单权限")
@has_permission("system:role:assign")
async def assign_menus(request: Request, role_id: int, body: RoleMenusRequest):
    await RbacService.assign_menus(role_id, body)
    return ApiResponse.success("分配成功")


@router.get("/roles/{role_id}/menus", summary="查询角色已分配菜单")
async def role_menus(role_id: int):
    return ApiResponse.success(data=await RbacService.role_menus(role_id))


# ==================== 菜单/权限 ====================
@router.get("/menus/tree", summary="菜单权限树（管理端全量）")
@has_permission("system:menu:list")
async def menu_tree(request: Request):
    return ApiResponse.success(data=await RbacService.menu_tree())


@router.post("/menus", summary="新增菜单/权限")
@has_permission("system:menu:add")
async def create_menu(request: Request, body: MenuCreateRequest):
    await RbacService.create_menu(body)
    return ApiResponse.success("创建成功")


@router.put("/menus/{menu_id}", summary="更新菜单/权限")
@has_permission("system:menu:edit")
async def update_menu(request: Request, menu_id: int, body: MenuUpdateRequest):
    await RbacService.update_menu(menu_id, body)
    return ApiResponse.success("更新成功")


@router.delete("/menus/{menu_id}", summary="删除菜单/权限")
@has_permission("system:menu:delete")
async def delete_menu(request: Request, menu_id: int):
    await RbacService.delete_menu(menu_id)
    return ApiResponse.success("删除成功")


# ==================== 部门（数据权限组织单元） ====================
@router.get("/depts/tree", summary="部门树")
@has_permission("system:dept:list")
async def dept_tree(request: Request):
    return ApiResponse.success(data=await RbacService.dept_tree())


@router.post("/depts", summary="新增部门")
@has_permission("system:dept:add")
async def create_dept(request: Request, body: DeptCreateRequest):
    await RbacService.create_dept(body)
    return ApiResponse.success("创建成功")


@router.put("/depts/{dept_id}", summary="更新部门")
@has_permission("system:dept:edit")
async def update_dept(request: Request, dept_id: int, body: DeptUpdateRequest):
    await RbacService.update_dept(dept_id, body)
    return ApiResponse.success("更新成功")


@router.delete("/depts/{dept_id}", summary="删除部门")
@has_permission("system:dept:delete")
async def delete_dept(request: Request, dept_id: int):
    await RbacService.delete_dept(dept_id)
    return ApiResponse.success("删除成功")