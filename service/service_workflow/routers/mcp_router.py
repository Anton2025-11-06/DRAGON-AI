from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import has_permission, get_login_user, require_permission
from common.common_permission.resource_guard import ACTION_TOOLS, ACTION_USE
from service.service_workflow.models.mcp import McpServerPageRequest, McpServerSaveRequest, McpServerTestRequest, \
    McpToolCallRequest
from service.service_workflow.services.mcp_service import McpServerService

router = APIRouter(prefix="/mcp-server", tags=["MCP连接"])


@router.post("/page", summary="分页查询 MCP 连接（POST 兼容）")
@has_permission("workflow:mcp:list")
async def page_mcp_post(request: Request, body: McpServerPageRequest):
    login_user = await get_login_user(request)
    data = await McpServerService.page(
        login_user, body.current, body.size, body.name, body.status)
    return ApiResponse.success(data=data)


@router.get("/options", summary="启用中 MCP 连接下拉（工作流节点用）")
@has_permission("workflow:mcp:list")
async def mcp_options(request: Request):
    """按 use 口径的下拉：与管理页 page（view 口径）分开，只被授到「使用MCP」的人
    在节点里也要选得到。清单不含 url/env，工具另走 /{mcp_id}/tools。"""
    login_user = await get_login_user(request)
    return ApiResponse.success(data=await McpServerService.options(login_user))


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
    try:
        await McpServerService.update(
            await get_login_user(request), mcp_id, body.name, body.type, body.url,
            body.command, body.args, body.env, body.status, body.description)
        return ApiResponse.success(message="保存成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.delete("/{mcp_id}", summary="删除 MCP 连接")
@has_permission("workflow:mcp:delete")
async def delete_mcp(request: Request, mcp_id: int):
    try:
        ok = await McpServerService.delete(await get_login_user(request), mcp_id)
        if not ok:
            return ApiResponse.error(400, "连接不存在或已删除")
        return ApiResponse.success(message="删除成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.patch("/{mcp_id}/status", summary="切换启用状态")
@has_permission("workflow:mcp:edit")
async def toggle_status(request: Request, mcp_id: int, status: bool = True):
    try:
        await McpServerService.toggle_status(await get_login_user(request), mcp_id, status)
        return ApiResponse.success(message="状态已更新")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.post("/{mcp_id}/test-connection", summary="按列表页测试连接")
@has_permission("workflow:mcp:test-external")
async def test_connection(request: Request, mcp_id: int):
    try:
        result = await McpServerService.test_by_id(await get_login_user(request), mcp_id)
        return ApiResponse.success(data=result)
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.post("/test-params", summary="编辑页测试连接")
@has_permission("workflow:mcp:test-internal")
async def test_params(request: Request, body: McpServerTestRequest):
    result = await McpServerService.test_params(
        name=body.name, type_=body.type, url=body.url,
        command=body.command, args=body.args, env=body.env)
    return ApiResponse.success(data=result)


@router.get("/{mcp_id}/tools", summary="获取MCP服务器工具列表")
@require_permission("workflow:mcp:toolList", "workflow:workflow:edit")
async def list_tools(request: Request, mcp_id: int):
    try:
        # 拉清单会真建连，两个动作都算数：「查看工具」是管理页那一档，「使用MCP」是
        # 工作流节点配参数的前提（拿不到工具就填不了入参）。停用状态下也得让人能排查，
        # 所以不走 ensure_usable
        row = await McpServerService.load_for_any(
            await get_login_user(request), mcp_id, [ACTION_TOOLS, ACTION_USE])
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    # 单服务器查询不走 batch_tools：那里为了多服务器并发故意吞异常，
    # 页面会拿不到原因只看到“暂无工具”；这里让真实报错直接透到前端
    tools = await McpServerService.list_tools(row, mcp_id)
    return ApiResponse.success(data=tools)


@router.post("/{mcp_id}/call-tool", summary="调用MCP工具")
@has_permission("workflow:mcp:call")
async def call_tool(request: Request, mcp_id: int, body: McpToolCallRequest):
    try:
        await McpServerService.load_for_action(
            await get_login_user(request), mcp_id, ACTION_USE)
        result = await McpServerService.call_tool(mcp_id, body.tool_name, body.arguments)
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    if result.get("isError"):
        return ApiResponse.error(500, result.get("content") or "工具调用返回错误")
    return ApiResponse.success(data=result)


