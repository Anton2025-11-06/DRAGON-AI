from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission, get_login_user
from service.service_workflow.services.tool_service import ToolService

router = APIRouter(prefix="/tools", tags=["工具管理"])


class ToolSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="工具名称")
    description: Optional[str] = Field(None, max_length=500, description="工具描述")
    function_code: str = Field(..., description="Python 函数源码")
    parameters_schema: Optional[str] = Field(
        None, description='参数定义 JSON：{"parameters":[{name,type,required,description,default}]}')
    status: bool = True
    timeout: Optional[int] = Field(None, ge=100, le=600000, description="执行超时（毫秒）")


class ToolTestRequest(BaseModel):
    parameters: Optional[dict] = Field(default_factory=dict, description="测试传入参数")


class ToolDefinitionTestRequest(BaseModel):
    """按定义测试：表单里未保存的代码 + 参数直接跑一次（新增/编辑弹窗的「测试」）"""
    function_code: str = Field(..., min_length=1, description="Python 函数源码")
    parameters: Optional[dict] = Field(default_factory=dict, description="测试参数")
    timeout: Optional[int] = Field(None, ge=100, le=600000, description="执行超时（毫秒）")


@router.get("/options", summary="启用中工具下拉（含参数定义）")
@has_permission("workflow:tool:list")
async def tool_options(request: Request):
    """供工作流「工具」节点：一次拿齐工具名与参数定义，表单才能自动列出入参。"""
    data = await ToolService.options()
    return ApiResponse.success(data=data)


@router.post("/test-definition", summary="按定义测试（未保存的工具代码）")
@has_permission("workflow:tool:test")
async def test_definition(request: Request, body: ToolDefinitionTestRequest):
    """与保存无关：让工具在弹窗里就能跑通，避开「先保存才能测」的往返。"""
    try:
        result = await ToolService.test_definition(
            body.function_code, body.parameters, body.timeout)
        return ApiResponse.success(data=result)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/page", summary="分页查询动态函数工具")
@has_permission("workflow:tool:list")
async def page_tools(request: Request, page: int = 1, page_size: int = 10,
                     name: str = None, status: bool = None):
    data = await ToolService.page(page, page_size, name, status)
    return ApiResponse.success(data=data)


@router.get("/{tool_id}/detail", summary="工具详情（完整源码）")
@has_permission("workflow:tool:list")
async def tool_detail(request: Request, tool_id: int):
    detail = await ToolService.detail(tool_id)
    if not detail:
        return ApiResponse.error(400, "工具不存在")
    # parameters 为摊平的参数定义（前端表单直接用，不必再解 JSON 字符串）
    detail["parameters"] = ToolService.parse_parameters(detail.pop("parameters_schema", None))
    return ApiResponse.success(data=detail)


@router.post("/create", summary="新增动态函数工具")
@has_permission("workflow:tool:add")
async def create_tool(request: Request, body: ToolSaveRequest):
    try:
        login_user = await get_login_user(request)
        new_id = await ToolService.create(
            body.name, body.function_code, body.description,
            body.parameters_schema, body.status, login_user.get("user_id") or 0,
            body.timeout)
        return ApiResponse.success(data={"id": new_id}, message="新增成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{tool_id}/modify", summary="修改动态函数工具")
@has_permission("workflow:tool:edit")
async def update_tool(request: Request, tool_id: int, body: ToolSaveRequest):
    try:
        await ToolService.update(tool_id, body.name, body.function_code,
                                 body.description, body.parameters_schema, body.status,
                                 body.timeout)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{tool_id}", summary="删除动态函数工具")
@has_permission("workflow:tool:delete")
async def delete_tool(request: Request, tool_id: int):
    ok = await ToolService.delete(tool_id)
    if not ok:
        return ApiResponse.error(400, "工具不存在或已删除")
    return ApiResponse.success(message="删除成功")


@router.patch("/{tool_id}/status", summary="切换启用状态")
@has_permission("workflow:tool:edit")
async def toggle_status(request: Request, tool_id: int, status: bool = True):
    await ToolService.toggle_status(tool_id, status)
    return ApiResponse.success(message="状态已更新")


@router.post("/{tool_id}/test", summary="运行测试动态函数工具")
@has_permission("workflow:tool:test")
async def test_tool(request: Request, tool_id: int, body: ToolTestRequest = None):
    body = body or ToolTestRequest()
    try:
        result = await ToolService.test(tool_id, body.parameters)
        return ApiResponse.success(data=result)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


