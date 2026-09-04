from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission, get_login_user
from service.service_workflow.services.mcp_service import McpServerService

router = APIRouter(prefix="/mcp-server", tags=["MCP连接管理"])


class McpServerSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="服务名称")
    type: str = Field("SSE", description="连接类型 SSE/STDIO")
    url: Optional[str] = Field(None, max_length=500, description="SSE 连接地址")
    command: Optional[str] = Field(None, max_length=255, description="STDIO 启动命令")
    args: Optional[str] = Field(None, max_length=1000, description="STDIO 参数 JSON 数组")
    env: Optional[str] = Field(None, max_length=1000, description="环境变量 JSON 对象")
    status: bool = True


class McpServerTestRequest(BaseModel):
    """按参数测试连接（添加/编辑表单测试按钮使用，无需保存）"""
    name: Optional[str] = None
    type: str = "SSE"
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[str] = None
    env: Optional[str] = None


class McpServerPageRequest(BaseModel):
    """分页查询请求体（前端 fast-crud POST 风格）"""
    current: int = 1
    size: int = 10
    name: Optional[str] = None
    status: Optional[bool] = None


@router.get("/page", summary="分页查询 MCP 连接")
@has_permission("workflow:mcp:list")
async def page_mcp(request: Request, page: int = 1, page_size: int = 10,
                   name: str = None, status: int = None):
    data = await McpServerService.page(page, page_size, name, status)
    return ApiResponse.success(data=data)


@router.post("/page", summary="分页查询 MCP 连接（POST 兼容）")
@has_permission("workflow:mcp:list")
async def page_mcp_post(request: Request, body: McpServerPageRequest):
    data = await McpServerService.page(
        body.current, body.size, body.name, _parse_status(body.status))
    return ApiResponse.success(data=data)


@router.post("/create", summary="新增 MCP 连接")
@has_permission("workflow:mcp:add")
async def create_mcp(request: Request, body: McpServerSaveRequest):
    login_user = await get_login_user(request)
    new_id = await McpServerService.create(
        body.name, body.type, body.url, body.command, body.args, body.env,
        body.status, login_user.get("user_id") or 0)
    return ApiResponse.success(data={"id": new_id}, message="新增成功")


@router.put("/{mcp_id}/modify", summary="修改 MCP 连接")
@has_permission("workflow:mcp:edit")
async def update_mcp(request: Request, mcp_id: int, body: McpServerSaveRequest):
    await McpServerService.update(mcp_id, body.name, body.type, body.url,
                                  body.command, body.args, body.env, body.status)
    return ApiResponse.success(message="保存成功")


@router.delete("/{mcp_id}", summary="删除 MCP 连接")
@has_permission("workflow:mcp:delete")
async def delete_mcp(request: Request, mcp_id: int):
    ok = await McpServerService.delete(mcp_id)
    if not ok:
        return ApiResponse.error(400, "连接不存在或已删除")
    return ApiResponse.success(message="删除成功")


@router.patch("/{mcp_id}/status", summary="切换启用状态")
@has_permission("workflow:mcp:edit")
async def toggle_status(request: Request, mcp_id: int, status: bool = True):
    await McpServerService.toggle_status(mcp_id, status)
    return ApiResponse.success(message="状态已更新")


@router.post("/{mcp_id}/test-connection", summary="按ID测试连接")
@has_permission("workflow:mcp:test")
async def test_connection(request: Request, mcp_id: int):
    result = await McpServerService.test_by_id(mcp_id)
    return ApiResponse.success(data=result)


@router.post("/test-params", summary="按参数测试连接(表单测试按钮)")
@has_permission("workflow:mcp:test")
async def test_params(request: Request, body: McpServerTestRequest):
    result = await McpServerService.test_params(
        name=body.name, type_=body.type, url=body.url,
        command=body.command, args=body.args, env=body.env)
    return ApiResponse.success(data=result)


@router.get("/{mcp_id}/tools", summary="获取MCP服务器工具列表")
@has_permission("workflow:mcp:list")
async def list_tools(request: Request, mcp_id: int):
    row = await McpServerService.get_by_id(mcp_id)
    if not row:
        return ApiResponse.error(400, "连接不存在")
    tools = await McpServerService.batch_tools([mcp_id])
    return ApiResponse.success(data=tools)


@router.patch("/{mcp_id}/refresh", summary="刷新MCP连接")
@has_permission("workflow:mcp:test")
async def refresh_connection(request: Request, mcp_id: int):
    row = await McpServerService.get_by_id(mcp_id)
    if not row:
        return ApiResponse.error(400, "连接不存在")
    return ApiResponse.success(message="刷新成功")


def _parse_status(status):
    """兼容 string/list 状态参数（fast-crud dict-switch 搜索框）"""
    if status is None or status == "":
        return None
    if isinstance(status, list):
        status = status[-1] if status else None
    if isinstance(status, str):
        return 1 if status.lower() in ("true", "1") else 0
    return 1 if status else 0