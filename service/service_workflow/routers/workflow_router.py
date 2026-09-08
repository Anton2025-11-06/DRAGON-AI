# -*- coding: utf-8 -*-
"""工作流定义域路由：CRUD / 发布 / 版本 / 回滚 / 校验 / 模板 / 下拉数据源。

与前端 ui-ai/.../api/ai-workflow/index.ts 契约一一对应；
网关 SERVICE_ALIASES 将 /api/workflow/* 剥前缀后转发到本服务 /*。
注意路由声明顺序：固定路径（/page、/node-definitions、/from-template/{id}）
必须先于 /{workflow_id}，否则会被动态段吞掉。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from common.common_constants.model_constant import MODEL_TYPE_TEXT
from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from service.service_workflow.schemas.workflow_schema import (
    TemplateSaveReq, WorkflowSaveReq,
)
from service.service_workflow.services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows", tags=["工作流编排"])


async def _user_id(request: Request) -> int:
    try:
        login_user = await get_login_user(request)
        return int(login_user.get("user_id") or 0)
    except Exception:  # noqa: BLE001
        return 0  # 直连/旁路调用（网关未注入用户）时降级为系统用户


# ==================== 固定路径（先声明） ====================

@router.get("/page", summary="分页查询工作流列表")
@has_permission("workflow:workflow:list")
async def page_workflows(request: Request, current: int = 1, size: int = 10,
                         name: str = None, status: str = None):
    data = await WorkflowService.page(current=current, size=size, name=name, status=status)
    return ApiResponse.success(data=data)


@router.get("/node-definitions", summary="获取节点类型定义（画布组件面板数据源）")
async def node_definitions(request: Request):
    return ApiResponse.success(data=WorkflowService.node_definitions())


@router.post("", summary="创建工作流")
@has_permission("workflow:workflow:add")
async def create_workflow(request: Request, body: WorkflowSaveReq):
    try:
        new_id = await WorkflowService.create(body, user_id=await _user_id(request))
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
            template_id, name, description, user_id=await _user_id(request))
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
    ok = await WorkflowService.update(workflow_id, body, user_id=await _user_id(request))
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
            workflow_id, change_log=change_log, user_id=await _user_id(request))
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
        new_id = await WorkflowService.copy(workflow_id, name, user_id=await _user_id(request))
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
        new_version = await WorkflowService.rollback(
            workflow_id, version, user_id=await _user_id(request))
        return ApiResponse.success(data={"version": new_version},
                                   message=f"已回滚并发布为 v{new_version}")
    except ValueError as e:
        return ApiResponse.error(400, str(e))


# ==================== 下拉数据源（模型 / 知识库 / 智能体） ====================

support_router = APIRouter(tags=["工作流编排"])


@support_router.get("/models/list", summary="模型下拉（当前用户审批通过的模型，按类型）")
async def list_models(request: Request, type: str = MODEL_TYPE_TEXT):
    try:
        # 只返回当前用户审批通过(status=1)的模型；未登录时 user_id=0 → 空列表
        return ApiResponse.success(
            data=await WorkflowService.list_models(type, await _user_id(request)))
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
    user_id = await _user_id(request)
    if not user_id:
        return ApiResponse.success(data=[])
    return ApiResponse.success(data=await WorkflowService.list_chat_agents(user_id))


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
    new_id = await WorkflowService.create_template(body, user_id=await _user_id(request))
    return ApiResponse.success(data=new_id, message="创建成功")


@template_router.post("/from-workflow/{workflow_id}", summary="从工作流创建模板")
@has_permission("workflow:template:add")
async def create_template_from_workflow(request: Request, workflow_id: int,
                                        name: str = Query(..., max_length=128),
                                        category: str = Query("CUSTOM", max_length=32),
                                        description: str = Query(None, max_length=500)):
    try:
        new_id = await WorkflowService.template_from_workflow(
            workflow_id, name, category, description, user_id=await _user_id(request))
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
        new_id = await WorkflowService.import_template(json_str, user_id=await _user_id(request))
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
