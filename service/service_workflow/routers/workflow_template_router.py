import json
from typing import Any

from fastapi import APIRouter, Query
from starlette.requests import Request

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission
from common.common_permission.permission import get_user_id
from service.service_workflow.schemas.workflow_schema import TemplateSaveReq
from service.service_workflow.services.workflow_service import WorkflowService

# ==================== 模板 ====================
# 前端契约前缀 /workflow-templates（不在 /workflows 之下，独立 router）

template_router = APIRouter(prefix="/workflow-templates", tags=["工作流模板"])


@template_router.get("/page", summary="分页查询模板")
async def page_templates(request: Request, current: int = 1, size: int = 10,
                         name: str = None, category: str = None,
                         builtInOnly: bool = False):
    data = await WorkflowService.template_page(current=current, size=size, name=name,
                                               category=category, built_in_only=builtInOnly)
    return ApiResponse.success(data=data)


@template_router.get("/built-in", summary="内置模板列表")
async def builtin_templates(request: Request):
    return ApiResponse.success(data=await WorkflowService.builtin_templates())


@template_router.get("/category/{category}", summary="按分类查询模板")
async def templates_by_category(request: Request, category: str):
    return ApiResponse.success(data=await WorkflowService.templates_by_category(category))


@template_router.post("", summary="创建模板")
@has_permission("workflow:template:add")
async def create_template(request: Request, body: TemplateSaveReq):
    new_id = await WorkflowService.create_template(body, user_id=await get_user_id(request))
    return ApiResponse.success(data=new_id, message="创建成功")


@template_router.post("/from-workflow/{workflow_id}", summary="从工作流创建模板")
@has_permission("workflow:template:add")
async def create_template_from_workflow(request: Request, workflow_id: int,
                                        name: str = Query(..., max_length=128),
                                        category: str = Query("CUSTOM", max_length=32),
                                        description: str = Query(None, max_length=500)):
    try:
        new_id = await WorkflowService.template_from_workflow(
            workflow_id, name, category, description, user_id=await get_user_id(request))
        return ApiResponse.success(data=new_id, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@template_router.post("/import", summary="导入模板（JSON 字符串）")
@has_permission("workflow:template:add")
async def import_template(request: Request, body: Any = None):
    # 前端以 application/json 提交模板 JSON 字符串：可能被序列为 JSON string，
    # 也可能直接是模板对象本身，两种形态都兼容
    json_str = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    try:
        new_id = await WorkflowService.import_template(json_str, user_id=await get_user_id(request))
        return ApiResponse.success(data=new_id, message="导入成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@template_router.get("/{template_id}", summary="模板详情")
async def template_detail(request: Request, template_id: int):
    data = await WorkflowService.template_detail(template_id)
    if data is None:
        return ApiResponse.error(400, "模板不存在")
    return ApiResponse.success(data=data)


@template_router.put("/{template_id}", summary="更新模板")
@has_permission("workflow:template:edit")
async def update_template(request: Request, template_id: int, body: TemplateSaveReq):
    try:
        ok = await WorkflowService.update_template(template_id, body)
        if not ok:
            return ApiResponse.error(400, "模板不存在")
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@template_router.delete("/{template_id}", summary="删除模板")
@has_permission("workflow:template:delete")
async def delete_template(request: Request, template_id: int):
    try:
        ok = await WorkflowService.delete_template(template_id)
        if not ok:
            return ApiResponse.error(400, "模板不存在")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@template_router.get("/{template_id}/export", summary="导出模板（JSON 字符串）")
async def export_template(request: Request, template_id: int):
    data = await WorkflowService.template_detail(template_id)
    if data is None:
        return ApiResponse.error(400, "模板不存在")
    payload = {"name": data.get("name"), "description": data.get("description"),
               "category": data.get("category"), "icon": data.get("icon"),
               "graph": data.get("graph")}
    return ApiResponse.success(data=json.dumps(payload, ensure_ascii=False, indent=2))
