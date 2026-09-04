# -*- coding: utf-8 -*-
"""模型广场路由：模型 CRUD（RBAC 权限）+ 申请审批（service_system 模块）"""

from fastapi import APIRouter, Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission, is_admin
from service.service_system.schemas.model_schema import (
    ApplyPageRequest, ModelApplyRequest, ModelAuditRequest, ModelPageRequest, ModelSaveRequest,
    ModelTestRequest,
)
from service.service_system.services.model_service import CATEGORIES, PROVIDERS, ModelService

router = APIRouter(prefix="/models", tags=["模型广场"])

# 权限标识（与 tb_menu 按钮权限点对应）
PERM_ADD = "system:model:add"
PERM_EDIT = "system:model:edit"
PERM_DELETE = "system:model:delete"
PERM_AUDIT = "system:model:audit"


# ==================== 模型 CRUD ====================
# 说明：列表/详情/分类为公开查询（登录即可，人人可看）；增删改需 RBAC 权限


@router.get("/page", summary="分页查询模型（登录即可）")
async def page_models(request: Request, page: int = 1, page_size: int = 10,
                      name: str = None, category: str = None,
                      provider: str = None, status: int = None):
    login_user = await get_login_user(request)
    body = ModelPageRequest(current=page, size=page_size, name=name,
                            category=category, provider=provider)
    if status is not None and status in (0, 1):
        body.status = bool(status)
    data = await ModelService.page(body, login_user.get("user_id"))
    return ApiResponse.success(data=data)


@router.post("/page", summary="分页查询模型 POST（前端 fast-crud 风格）")
async def page_models_post(request: Request, body: ModelPageRequest):
    login_user = await get_login_user(request)
    data = await ModelService.page(body, login_user.get("user_id"))
    return ApiResponse.success(data=data)


@router.get("/categories", summary="分类与提供商字典")
async def categories(request: Request):
    return ApiResponse.success(data={
        "categories": [{"value": k, "label": v} for k, v in CATEGORIES.items()],
        "providers": [{"value": k, "label": v} for k, v in PROVIDERS.items()],
    })


@router.get("/my-keys", summary="我的 API Key（申请通过后可见）")
async def my_keys(request: Request):
    login_user = await get_login_user(request)
    data = await ModelService.my_keys(login_user.get("user_id") or 0)
    return ApiResponse.success(data=data)


@router.post("/test", summary="连通性测试（登录即可，走 OpenAI 兼容探测端点）")
async def test_model(request: Request, body: ModelTestRequest):
    try:
        data = await ModelService.test(body)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data=data, message="测试完成")


@router.get("/{model_id}/detail", summary="模型详情（登录即可，密钥仅管理员可见）")
async def detail_model(request: Request, model_id: int):
    login_user = await get_login_user(request)
    with_secret = is_admin(login_user)
    try:
        data = await ModelService.detail(model_id, with_secret=with_secret)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data=data)


@router.post("/create", summary="新增模型")
@has_permission(PERM_ADD)
async def create_model(request: Request, body: ModelSaveRequest):
    login_user = await get_login_user(request)
    try:
        new_id = await ModelService.create(body, login_user.get("user_id") or 0)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data={"id": new_id}, message="新增成功")


@router.put("/{model_id}/modify", summary="修改模型")
@has_permission(PERM_EDIT)
async def update_model(request: Request, model_id: int, body: ModelSaveRequest):
    try:
        await ModelService.update(model_id, body)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(message="保存成功")


@router.patch("/{model_id}/status", summary="切换启用状态")
@has_permission(PERM_EDIT)
async def toggle_model_status(request: Request, model_id: int, status: bool = True):
    ok = await ModelService.toggle_status(model_id, status)
    if not ok:
        return ApiResponse.error(400, "模型不存在")
    return ApiResponse.success(message="状态已更新")


@router.delete("/{model_id}", summary="删除模型")
@has_permission(PERM_DELETE)
async def delete_model(request: Request, model_id: int):
    ok = await ModelService.delete(model_id)
    if not ok:
        return ApiResponse.error(400, "模型不存在或已删除")
    return ApiResponse.success(message="删除成功")


# ==================== 申请审批 ====================

@router.post("/applies/page", summary="申请审批列表（POST）")
@has_permission(PERM_AUDIT)
async def applies_page(request: Request, body: ApplyPageRequest):
    data = await ModelService.applies_page(body)
    return ApiResponse.success(data=data)


@router.get("/applies/page", summary="申请审批列表（GET）")
@has_permission(PERM_AUDIT)
async def applies_page_get(request: Request, page: int = 1, page_size: int = 10,
                           status: int = None, model_id: int = None):
    body = ApplyPageRequest(current=page, size=page_size, status=status, model_id=model_id)
    data = await ModelService.applies_page(body)
    return ApiResponse.success(data=data)


@router.post("/applies/{apply_id}/audit", summary="审批申请（通过时后台创建 api-key）")
@has_permission(PERM_AUDIT)
async def audit_apply(request: Request, apply_id: int, body: ModelAuditRequest):
    login_user = await get_login_user(request)
    try:
        data = await ModelService.audit(
            apply_id, body, login_user.get("username") or "-")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data=data, message="审批完成")


@router.post("/{model_id}/apply", summary="申请使用模型（登录即可）")
async def apply_model(request: Request, model_id: int, body: ModelApplyRequest):
    login_user = await get_login_user(request)
    try:
        apply_id = await ModelService.apply(
            model_id, login_user.get("user_id") or 0,
            login_user.get("username") or "", login_user.get("dept_id") or 0, body)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    return ApiResponse.success(data={"apply_id": apply_id}, message="申请已提交，等待管理员审批")