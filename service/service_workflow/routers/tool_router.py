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
    parameters_schema: Optional[str] = Field(None, max_length=2000, description="参数说明 JSON")
    status: bool = True


class ToolTestRequest(BaseModel):
    parameters: Optional[dict] = Field(default_factory=dict, description="测试传入参数")


@router.get("/page", summary="分页查询动态函数工具")
@has_permission("workflow:tool:list")
async def page_tools(request: Request, page: int = 1, page_size: int = 10,
                     name: str = None, status: int = None):
    data = await ToolService.page(page, page_size, name, status)
    return ApiResponse.success(data=data)


@router.get("/{tool_id}/detail", summary="工具详情（完整源码）")
@has_permission("workflow:tool:list")
async def tool_detail(request: Request, tool_id: int):
    row = await ToolService.get_by_id(tool_id)
    if not row:
        return ApiResponse.error(400, "工具不存在")
    return ApiResponse.success(data={
        "id": row[0], "name": row[1], "description": row[2],
        "function_code": row[3], "parameters_schema": row[4],
        "status": bool(row[5]),
    })


@router.post("/create", summary="新增动态函数工具")
@has_permission("workflow:tool:add")
async def create_tool(request: Request, body: ToolSaveRequest):
    try:
        login_user = await get_login_user(request)
        new_id = await ToolService.create(
            body.name, body.function_code, body.description,
            body.parameters_schema, body.status, login_user.get("user_id") or 0)
        return ApiResponse.success(data={"id": new_id}, message="新增成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.put("/{tool_id}/modify", summary="修改动态函数工具")
@has_permission("workflow:tool:edit")
async def update_tool(request: Request, tool_id: int, body: ToolSaveRequest):
    try:
        await ToolService.update(tool_id, body.name, body.function_code,
                                 body.description, body.parameters_schema, body.status)
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