# -*- coding: utf-8 -*-
"""工作流定义域路由：CRUD / 发布 / 版本 / 回滚 / 校验 / 模板 / 下拉数据源。

与前端 ui-ai/.../api/ai-workflow/index.ts 契约一一对应；
网关 SERVICE_ALIASES 将 /api/workflow/* 剥前缀后转发到本服务 /*。
注意路由声明顺序：固定路径（/page、/node-definitions、/executable-list、/from-template/{id}）
必须先于 /{workflow_id}，否则会被动态段吞掉。
"""
from __future__ import annotations


from fastapi import APIRouter, Query, Request

from common.common_constants.model_constant import MODEL_TYPE_TEXT
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_user_id, has_permission
from service.service_workflow.schemas.workflow_schema import (
     WorkflowSaveReq,
)
from service.service_workflow.services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows", tags=["工作流编排"])



# ==================== 固定路径（先声明） ====================

@router.get("/page", summary="分页查询工作流列表")
@has_permission("workflow:workflow:list")
async def page_workflows(request: Request, page: int = 1, page_size: int = 10,
                         name: str = None, status: str = None):
    data = await WorkflowService.page(current=page, size=page_size, name=name, status=status)
    return ApiResponse.success(data=data)


@router.get("/node-definitions", summary="获取节点类型定义（画布组件面板数据源）")
async def node_definitions(request: Request):
    return ApiResponse.success(data=WorkflowService.node_definitions())


@router.get("/executable-list", summary="可调用工作流下拉（【工作流】节点数据源）")
@has_permission("workflow:workflow:list")
async def executable_workflows(request: Request):
    """已发布且配有可用 API Key 的工作流（附可用 key 清单）。

    版本下拉不单独开口：复用 GET /workflows/{id}/versions。
    """
    return ApiResponse.success(data=await WorkflowService.list_executable())


@router.post("", summary="创建工作流")
@has_permission("workflow:workflow:add")
async def create_workflow(request: Request, body: WorkflowSaveReq):
    try:
        new_id = await WorkflowService.create(body, user_id=await get_user_id(request))
        return ApiResponse.success(data=new_id, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.post("/from-template/{template_id}", summary="从模板创建工作流")
@has_permission("workflow:workflow:add")
async def create_from_template(request: Request, template_id: int,
                               name: str = Query(None, max_length=128),
                               description: str = Query(None, max_length=500)):
    try:
        new_id = await WorkflowService.create_from_template(
            template_id, name, description, user_id=await get_user_id(request))
        return ApiResponse.success(data=new_id, message="创建成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


# ==================== 单个工作流 ====================

@router.get("/{workflow_id}", summary="获取工作流详情")
@has_permission("workflow:workflow:list")
async def workflow_detail(request: Request, workflow_id: int):
    data = await WorkflowService.detail(workflow_id)
    if data is None:
        return ApiResponse.error(400, "工作流不存在")
    return ApiResponse.success(data=data)


@router.put("/{workflow_id}", summary="更新工作流（草稿保存）")
@has_permission("workflow:workflow:edit")
async def update_workflow(request: Request, workflow_id: int, body: WorkflowSaveReq):
    ok = await WorkflowService.update(workflow_id, body, user_id=await get_user_id(request))
    if not ok:
        return ApiResponse.error(400, "工作流不存在")
    return ApiResponse.success(message="保存成功")


@router.delete("/{workflow_id}", summary="删除工作流（级联版本与API Key）")
@has_permission("workflow:workflow:delete")
async def delete_workflow(request: Request, workflow_id: int):
    ok = await WorkflowService.delete(workflow_id)
    if not ok:
        return ApiResponse.error(400, "工作流不存在")
    return ApiResponse.success(message="删除成功")


@router.post("/{workflow_id}/publish", summary="发布工作流（快照+版本号）")
@has_permission("workflow:workflow:publish")
async def publish_workflow(request: Request, workflow_id: int, body: dict = None):
    try:
        change_log = (body or {}).get("changeLog") if isinstance(body, dict) else None
        result = await WorkflowService.publish(
            workflow_id, change_log=change_log, user_id=await get_user_id(request))
        return ApiResponse.success(data=result, message=f"发布成功，当前版本 v{result['version']}")
    except ValueError as e:
        return ApiResponse.error(400, str(e))
    except Exception as e:  # noqa: BLE001  图校验错误 WorkflowGraphError
        return ApiResponse.error(400, str(e))


@router.post("/{workflow_id}/copy", summary="复制工作流")
@has_permission("workflow:workflow:add")
async def copy_workflow(request: Request, workflow_id: int,
                        name: str = Query(None, max_length=128)):
    try:
        new_id = await WorkflowService.copy(workflow_id, name, user_id=await get_user_id(request))
        return ApiResponse.success(data=new_id, message="复制成功")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


@router.get("/{workflow_id}/validate", summary="校验工作流图（错误/警告清单）")
@has_permission("workflow:workflow:list")
async def validate_workflow(request: Request, workflow_id: int):
    try:
        return ApiResponse.success(data=await WorkflowService.validate_graph(workflow_id))
    except ValueError as e:
        return ApiResponse.error(400, str(e))


# ==================== 版本 ====================

@router.get("/{workflow_id}/versions", summary="获取版本历史")
@has_permission("workflow:workflow:list")
async def workflow_versions(request: Request, workflow_id: int):
    return ApiResponse.success(data=await WorkflowService.versions(workflow_id))


@router.get("/{workflow_id}/versions/{version}", summary="获取指定版本快照")
@has_permission("workflow:workflow:list")
async def workflow_version_detail(request: Request, workflow_id: int, version: int):
    data = await WorkflowService.version_detail(workflow_id, version)
    if data is None:
        return ApiResponse.error(400, "版本不存在")
    return ApiResponse.success(data=data)


@router.post("/{workflow_id}/rollback/{version}", summary="回滚到指定版本")
@has_permission("workflow:workflow:publish")
async def rollback_workflow(request: Request, workflow_id: int, version: int):
    try:
        await WorkflowService.rollback(
            workflow_id, version, user_id=await get_user_id(request))
        return ApiResponse.success(message=f"已恢复 v{version}，发布请手动触发")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


# ==================== 下拉数据源（模型 / 知识库 / 智能体） ====================

support_router = APIRouter(tags=["工作流编排"])


@support_router.get("/models/list", summary="模型下拉（当前用户审批通过的模型，按类型）")
async def list_models(request: Request, type: str = MODEL_TYPE_TEXT):
    try:
        # 只返回当前用户审批通过(status=1)的模型；未登录时 user_id=0 → 空列表
        return ApiResponse.success(
            data=await WorkflowService.list_models(type, await get_user_id(request)))
    except Exception as e:  # noqa: BLE001
        return ApiResponse.success(data=[])  # 下拉失败不阻断画布加载


@support_router.get("/knowledge-bases/list", summary="知识库下拉")
async def list_knowledge_bases(request: Request):
    try:
        return ApiResponse.success(data=await WorkflowService.list_knowledge_bases())
    except Exception as e:  # noqa: BLE001
        return ApiResponse.success(data=[])


@support_router.get("/chat-agents/mine", summary="我的智能体下拉")
async def list_chat_agents(request: Request):
    user_id = await get_user_id(request)
    if not user_id:
        return ApiResponse.success(data=[])
    return ApiResponse.success(data=await WorkflowService.list_chat_agents(user_id))

