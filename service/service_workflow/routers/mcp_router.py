from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission, get_login_user, is_admin
from service.service_workflow.services.mcp_service import McpServerService

router = APIRouter(prefix="/mcp-server", tags=["MCP连接"])


class McpServerSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="服务名称")
    type: str = Field("SSE", description="连接类型 SSE/STDIO")
    url: Optional[str] = Field(None, max_length=500, description="SSE 连接地址")
    command: Optional[str] = Field(None, max_length=255, description="STDIO 启动命令")
    args: Optional[str] = Field(None, max_length=1000, description="STDIO 参数 JSON 数组")
    env: Optional[str] = Field(None, max_length=1000, description="环境变量 JSON 对象")
    description: Optional[str] = Field(None, max_length=500, description="MCP 描述（这个连接是做什么的）")
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


class McpToolCallRequest(BaseModel):
    """调用 MCP 工具（工作流工具节点 / 页面调试）"""
    tool_name: str = Field(..., min_length=1, max_length=128, description="工具名称")
    arguments: dict = Field(default_factory=dict, description="调用参数 JSON 对象")


@router.post("/page", summary="分页查询 MCP 连接（POST 兼容）")
@has_permission("workflow:mcp:list")
async def page_mcp_post(request: Request, body: McpServerPageRequest):
    login_user = await get_login_user(request)
    data = await McpServerService.page(
        body.current, body.size, body.name, body.status,
        viewer_id=int(login_user.get("user_id") or 0), viewer_admin=is_admin(login_user))
    return ApiResponse.success(data=data)


@router.post("/create", summary="新增 MCP 连接")
@has_permission("workflow:mcp:add")
async def create_mcp(request: Request, body: McpServerSaveRequest):
    login_user = await get_login_user(request)
    new_id = await McpServerService.create(
        body.name, body.type, body.url, body.command, body.args, body.env,
        body.status, login_user.get("user_id") or 0, body.description)
    return ApiResponse.success(data={"id": new_id}, message="新增成功")


@router.put("/{mcp_id}/modify", summary="修改 MCP 连接")
@has_permission("workflow:mcp:edit")
async def update_mcp(request: Request, mcp_id: int, body: McpServerSaveRequest):
    await McpServerService.update(mcp_id, body.name, body.type, body.url,
                                  body.command, body.args, body.env, body.status,
                                  body.description)
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


@router.post("/{mcp_id}/test-connection", summary="按列表页测试连接")
@has_permission("workflow:mcp:test-external")
async def test_connection(request: Request, mcp_id: int):
    result = await McpServerService.test_by_id(mcp_id)
    return ApiResponse.success(data=result)


@router.post("/test-params", summary="编辑页测试连接")
@has_permission("workflow:mcp:test-internal")
async def test_params(request: Request, body: McpServerTestRequest):
    result = await McpServerService.test_params(
        name=body.name, type_=body.type, url=body.url,
        command=body.command, args=body.args, env=body.env)
    return ApiResponse.success(data=result)


@router.get("/{mcp_id}/tools", summary="获取MCP服务器工具列表")
@has_permission("workflow:mcp:toolList")
async def list_tools(request: Request, mcp_id: int):
    row = await McpServerService.get_by_id(mcp_id)
    if not row:
        return ApiResponse.error(400, "连接不存在")
    # 单服务器查询不走 batch_tools：那里为了多服务器并发故意吞异常，
    # 页面会拿不到原因只看到“暂无工具”；这里让真实报错直接透到前端
    tools = await McpServerService.list_tools(row, mcp_id)
    return ApiResponse.success(data=tools)


@router.post("/{mcp_id}/call-tool", summary="调用MCP工具")
@has_permission("workflow:mcp:call")
async def call_tool(request: Request, mcp_id: int, body: McpToolCallRequest):
    row = await McpServerService.get_by_id(mcp_id)
    if not row:
        return ApiResponse.error(400, "连接不存在")
    result = await McpServerService.call_tool(mcp_id, body.tool_name, body.arguments)
    if result.get("isError"):
        return ApiResponse.error(500, result.get("content") or "工具调用返回错误")
    return ApiResponse.success(data=result)


