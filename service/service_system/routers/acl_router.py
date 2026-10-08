"""资源实例授权（tb_resource_acl）HTTP 接口 —— 全系统唯一的授权读写口。

调用方是各资源列表页的「授权」弹窗（知识库/文档/工作流/模板/工具/技能/MCP）。
门禁分两层，缺一不可：
- 功能权限点（ai:kb:grant / workflow:tool:grant …）：决定这个人在页面上有没有授权入口；
- 数据权限 share（由 AclService 回调 resource_guard.ensure_action_strict）：
  决定他能不能动「这一条」资源的授权。
只有前者，等于有授权页的人可以给任何人的库授权；只有后者，等于按钮永远亮但点了 403。
"""
from fastapi import APIRouter, Query, Request

from common.common_entity.response_schema import ApiResponse
from common.common_exception.custom_exception import UnauthorizedException
from common.common_permission.permission import (
    get_login_user, is_admin, require_permission)
from service.service_system.schemas.acl_schema import (
    AclGrantsAppendRequest, AclGrantsSaveRequest)
from service.service_system.services.acl_service import GRANT_PERMISSIONS, AclService

router = APIRouter(tags=["资源授权"])

# 装饰器在定义期只能给出静态权限串，所以接口级先卡「任一资源的授权权限」，
# 具体是哪一类资源再由 _need_grant_perm 在 handler 里卡（否则会写出
# 「只有工具授权权限的人给知识库授权」这种串门）
_ALL_GRANT_PERMS = tuple(GRANT_PERMISSIONS.values())


def _need_grant_perm(login_user: dict, resource_code: str) -> None:
    """按 resource_code 卡对应那一类的授权权限点（ADMIN 放行，与 permission 装饰器同规则）"""
    perm = GRANT_PERMISSIONS.get(resource_code)
    if not perm:
        # 不返回空结果糊弄过去：未登记或本就不支持授权的资源类型，是调用方写错了
        raise ValueError(f"不支持授权的资源类型：{resource_code}"
                         f"（已支持 {list(GRANT_PERMISSIONS)}）")
    if not is_admin(login_user) and perm not in login_user.get("permissions", []):
        raise UnauthorizedException(f"缺少该资源的授权入口权限：{perm}")


@router.get("/acl/resources", summary="可授权资源类型与动作清单")
async def acl_resources(request: Request, resource_code: str = Query(None, max_length=32,
                                                                     description="留空=全部类型")):
    """下发 RESOURCE_SPECS 的动作/主体类型，前端不硬编码动作码

    只登录不卡权限点：这一份是常量清单（不含任何数据），授权弹窗要先渲染出勾选框，
    才知道该往哪个权限点去要权限。
    """
    await get_login_user(request)
    return ApiResponse.success(data=AclService.resource_meta(resource_code))


@router.get("/acl/grantees", summary="授权主体候选（用户/角色/部门/用户组/全员）")
@require_permission(*_ALL_GRANT_PERMS)
async def acl_grantees(request: Request,
                       grantee_type: int = Query(..., ge=1, le=5,
                                                 description="1-用户 2-角色 3-部门 4-用户组 5-全员"),
                       keyword: str = Query(None, max_length=64, description="名称模糊查询"),
                       limit: int = Query(20, ge=1, le=100, description="返回条数")):
    login_user = await get_login_user(request)
    return ApiResponse.success(data=await AclService.grantee_options(
        grantee_type, keyword, limit, login_user=login_user))


@router.get("/acl/grants", summary="查询某条资源的在册授权")
@require_permission(*_ALL_GRANT_PERMS)
async def acl_list(request: Request,
                   resource_code: str = Query(..., max_length=32, description="资源类型编码"),
                   resource_id: int = Query(..., gt=0, description="资源实例主键")):
    login_user = await get_login_user(request)
    _need_grant_perm(login_user, resource_code)
    return ApiResponse.success(data=await AclService.list_grants(
        login_user, resource_code, resource_id))


@router.put("/acl/grants", summary="整表提交授权（差集即撤销）")
@require_permission(*_ALL_GRANT_PERMS)
async def acl_save(request: Request, body: AclGrantsSaveRequest):
    """授权弹窗「保存」：grants 是这条资源授权的目标全集，空数组=清空全部显式授权"""
    login_user = await get_login_user(request)
    _need_grant_perm(login_user, body.resource_code)
    data = await AclService.save_grants(
        login_user, body.resource_code, body.resource_id, body.grants)
    return ApiResponse.success(data=data, message="授权已更新")


@router.post("/acl/grants", summary="追加授权（不动在册行）")
@require_permission(*_ALL_GRANT_PERMS)
async def acl_append(request: Request, body: AclGrantsAppendRequest):
    login_user = await get_login_user(request)
    _need_grant_perm(login_user, body.resource_code)
    data = await AclService.add_grants(
        login_user, body.resource_code, body.resource_id, body.grants)
    return ApiResponse.success(data=data, message="授权已追加")


@router.delete("/acl/grants", summary="撤销某个主体的授权")
@require_permission(*_ALL_GRANT_PERMS)
async def acl_revoke(request: Request,
                     resource_code: str = Query(..., max_length=32, description="资源类型编码"),
                     resource_id: int = Query(..., gt=0, description="资源实例主键"),
                     grantee_type: int = Query(..., ge=1, le=5, description="授权主体类型"),
                     grantee_id: int = Query(0, ge=0, description="主体ID，全员固定 0"),
                     actions: str = Query(None,
                                          description="逗号分隔动作码，留空=撤销该主体全部动作")):
    """主体级撤销：整行删除授权时用（逐动作删除请走 PUT 整表提交）"""
    login_user = await get_login_user(request)
    _need_grant_perm(login_user, resource_code)
    codes = [a.strip() for a in (actions or "").split(",") if a.strip()]
    revoked = await AclService.revoke_grants(
        login_user, resource_code, resource_id, grantee_type, grantee_id, codes or None)
    return ApiResponse.success(data={"revoked": revoked}, message="授权已撤销")
