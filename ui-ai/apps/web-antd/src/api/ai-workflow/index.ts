/**
 * AI 工作流 API
 * 提供工作流定义、执行、模板管理的 API 接口
 */

import type {
  AiModelOption,
  KnowledgeBaseOption,
  McpServerOption,
  McpToolOption,
  WorkflowAgentOption,
  WorkflowDetailResp,
  WorkflowExecutionPageReq,
  WorkflowExecutionReq,
  WorkflowExecutionResp,
  WorkflowNodeDefinitionResp,
  WorkflowPageReq,
  WorkflowPageResp,
  WorkflowSaveReq,
  WorkflowTemplatePageReq,
  WorkflowTemplateResp,
  WorkflowTemplateSaveReq,
  WorkflowVersionResp,
} from './types';

import type { PageResult } from '#/api/common';

import { resolveApiUrl } from '#/api/helper';

import { requestClient } from '#/api/request';

const BASE_URL = '/api/workflow';

// ==================== 工作流定义 API ====================

/**
 * 分页查询工作流列表
 */
export function getWorkflowPage(params?: WorkflowPageReq) {
  return requestClient.get<PageResult<WorkflowPageResp>>(
    `${BASE_URL}/workflows/page`,
    { params },
  );
}

/**
 * 获取工作流详情
 */
export function getWorkflowDetail(id: number | string) {
  return requestClient.get<WorkflowDetailResp>(`${BASE_URL}/workflows/${id}`);
}

export function getWorkflowNodeDefinitions() {
  return requestClient.get<WorkflowNodeDefinitionResp[]>(
    `${BASE_URL}/workflows/node-definitions`,
  );
}

export function listAiModels(type = 'text') {
  return requestClient.get<AiModelOption[]>(`${BASE_URL}/models/list`, {
    params: { type },
  });
}

export function listWorkflowAgents() {
  return requestClient.get<WorkflowAgentOption[]>(
    `${BASE_URL}/chat-agents/mine`,
  );
}

export function listKnowledgeBases() {
  return requestClient.get<KnowledgeBaseOption[]>(
    `${BASE_URL}/knowledge-bases/list`,
  );
}

export function listMcpServers() {
  return requestClient
    .post<McpServerOption[] | { records?: McpServerOption[] }>(
      `${BASE_URL}/mcp-server/page`,
      { current: 1, size: 100 },
    )
    .then((resp) => (Array.isArray(resp) ? resp : resp.records || []));
}

export function listMcpServerTools(serverId: number | string) {
  return requestClient.get<McpToolOption[]>(
    `${BASE_URL}/mcp-server/${serverId}/tools`,
  );
}

/**
 * 创建工作流
 */
export function createWorkflow(data: WorkflowSaveReq) {
  return requestClient.post<number>(`${BASE_URL}/workflows`, data);
}

/**
 * 更新工作流
 */
export function updateWorkflow(id: number | string, data: WorkflowSaveReq) {
  return requestClient.put<void>(`${BASE_URL}/workflows/${id}`, data);
}

/**
 * 删除工作流
 */
export function deleteWorkflow(id: number | string) {
  return requestClient.delete<void>(`${BASE_URL}/workflows/${id}`);
}

/**
 * 发布工作流
 */
export function publishWorkflow(id: number | string) {
  return requestClient.post<void>(`${BASE_URL}/workflows/${id}/publish`);
}

/**
 * 归档工作流
 */
export function archiveWorkflow(id: number | string) {
  return requestClient.post<void>(`${BASE_URL}/workflows/${id}/archive`);
}

/**
 * 复制工作流
 */
export function copyWorkflow(id: number | string, name: string) {
  return requestClient.post<number>(`${BASE_URL}/workflows/${id}/copy`, null, {
    params: { name },
  });
}

// ==================== 版本管理 API ====================

/**
 * 获取工作流版本历史
 */
export function getWorkflowVersionHistory(id: number | string) {
  return requestClient.get<WorkflowVersionResp[]>(
    `${BASE_URL}/workflows/${id}/versions`,
  );
}

/**
 * 获取指定版本详情
 */
export function getWorkflowVersion(id: number | string, version: number) {
  return requestClient.get<WorkflowVersionResp>(
    `${BASE_URL}/workflows/${id}/versions/${version}`,
  );
}

/**
 * 回滚到指定版本
 */
export function rollbackWorkflow(id: number | string, version: number) {
  return requestClient.post<void>(
    `${BASE_URL}/workflows/${id}/rollback/${version}`,
  );
}

/**
 * 从模板创建工作流
 */
export function createWorkflowFromTemplate(
  templateId: number | string,
  name: string,
  description?: string,
) {
  return requestClient.post<number>(
    `${BASE_URL}/workflows/from-template/${templateId}`,
    null,
    { params: { name, description } },
  );
}

// ==================== 工作流执行 API ====================

/**
 * 同步执行工作流
 */
export function executeWorkflow(
  workflowId: number | string,
  data: WorkflowExecutionReq,
) {
  return requestClient.post<WorkflowExecutionResp>(
    `${BASE_URL}/workflow-executions/workflows/${workflowId}/execute`,
    data,
  );
}

/**
 * 异步执行工作流
 */
export function executeWorkflowAsync(
  workflowId: number | string,
  data: WorkflowExecutionReq,
) {
  return requestClient.post<string>(
    `${BASE_URL}/workflow-executions/workflows/${workflowId}/execute-async`,
    data,
  );
}

/**
 * 获取执行详情
 */
export function getExecution(executionId: string) {
  return requestClient.get<WorkflowExecutionResp>(
    `${BASE_URL}/workflow-executions/${executionId}`,
  );
}

/**
 * 分页查询执行历史
 */
export function getExecutionPage(params?: WorkflowExecutionPageReq) {
  return requestClient.get<PageResult<WorkflowExecutionResp>>(
    `${BASE_URL}/workflow-executions/page`,
    { params },
  );
}

/**
 * 获取工作流执行历史
 */
export function getWorkflowExecutions(
  workflowId: number | string,
  params?: WorkflowExecutionPageReq,
) {
  return requestClient.get<PageResult<WorkflowExecutionResp>>(
    `${BASE_URL}/workflow-executions/workflows/${workflowId}/executions`,
    { params },
  );
}

// ==================== 执行控制 API ====================

/**
 * 暂停执行
 */
export function pauseExecution(executionId: string) {
  return requestClient.post<void>(
    `${BASE_URL}/workflow-executions/${executionId}/pause`,
  );
}

/**
 * 恢复执行
 */
export function resumeExecution(executionId: string) {
  return requestClient.post<void>(
    `${BASE_URL}/workflow-executions/${executionId}/resume`,
  );
}

/**
 * 取消执行
 */
export function cancelExecution(executionId: string) {
  return requestClient.post<void>(
    `${BASE_URL}/workflow-executions/${executionId}/cancel`,
  );
}

// ==================== 调试控制 API ====================

/**
 * 更新变量
 */
export function updateVariable(
  executionId: string,
  variableName: string,
  value: any,
) {
  return requestClient.put<void>(
    `${BASE_URL}/workflow-executions/${executionId}/variables/${variableName}`,
    value,
  );
}

/**
 * 获取执行快照
 */
export function getExecutionSnapshot(executionId: string) {
  return requestClient.get<Record<string, any>>(
    `${BASE_URL}/workflow-executions/${executionId}/snapshot`,
  );
}

/**
 * 从快照恢复执行
 */
export function resumeFromSnapshot(
  executionId: string,
  snapshot: Record<string, any>,
) {
  return requestClient.post<WorkflowExecutionResp>(
    `${BASE_URL}/workflow-executions/${executionId}/resume-from-snapshot`,
    snapshot,
  );
}

// ==================== SSE 订阅 ====================

/**
 * 创建执行事件订阅
 * @param executionId 执行ID
 * @returns SSE URL
 */
export function getExecutionSubscribeUrl(
  executionId: string,
  baseUrl = '',
): string {
  return resolveApiUrl(
    `${BASE_URL}/workflow-executions/${executionId}/subscribe`,
    baseUrl,
  );
}

// ==================== 工作流模板 API ====================

/**
 * 分页查询模板列表
 */
export function getTemplatePage(params?: WorkflowTemplatePageReq) {
  return requestClient.get<PageResult<WorkflowTemplateResp>>(
    `${BASE_URL}/workflow-templates/page`,
    { params },
  );
}

/**
 * 获取模板详情
 */
export function getTemplateDetail(id: number | string) {
  return requestClient.get<WorkflowTemplateResp>(
    `${BASE_URL}/workflow-templates/${id}`,
  );
}

/**
 * 获取内置模板列表
 */
export function getBuiltInTemplates() {
  return requestClient.get<WorkflowTemplateResp[]>(
    `${BASE_URL}/workflow-templates/built-in`,
  );
}

/**
 * 按分类获取模板列表
 */
export function getTemplatesByCategory(category: string) {
  return requestClient.get<WorkflowTemplateResp[]>(
    `${BASE_URL}/workflow-templates/category/${category}`,
  );
}

/**
 * 创建模板
 */
export function createTemplate(data: WorkflowTemplateSaveReq) {
  return requestClient.post<number>(`${BASE_URL}/workflow-templates`, data);
}

/**
 * 从工作流创建模板
 */
export function createTemplateFromWorkflow(
  workflowId: number | string,
  name: string,
  category: string,
  description?: string,
) {
  return requestClient.post<number>(
    `${BASE_URL}/workflow-templates/from-workflow/${workflowId}`,
    null,
    { params: { name, description, category } },
  );
}

/**
 * 更新模板
 */
export function updateTemplate(
  id: number | string,
  data: WorkflowTemplateSaveReq,
) {
  return requestClient.put<void>(`${BASE_URL}/workflow-templates/${id}`, data);
}

/**
 * 删除模板
 */
export function deleteTemplate(id: number | string) {
  return requestClient.delete<void>(`${BASE_URL}/workflow-templates/${id}`);
}

/**
 * 导出模板
 */
export function exportTemplate(id: number | string) {
  return requestClient.get<string>(
    `${BASE_URL}/workflow-templates/${id}/export`,
  );
}

/**
 * 导入模板
 */
export function importTemplate(json: string) {
  return requestClient.post<number>(
    `${BASE_URL}/workflow-templates/import`,
    json,
    {
      headers: { 'Content-Type': 'application/json' },
    },
  );
}

// ==================== 工作流文件 API ====================

/**
 * 文件上传响应
 */
export interface WorkflowFileInfo {
  fileId: string;
  name: string;
  size: number;
  contentType: string;
  uploadTime: string;
}

/**
 * 上传工作流文件
 */
export function uploadWorkflowFile(file: File): Promise<WorkflowFileInfo> {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<WorkflowFileInfo>(
    `${BASE_URL}/workflow-files/upload`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
}

/**
 * 批量上传工作流文件
 */
export function uploadWorkflowFiles(
  files: File[],
): Promise<WorkflowFileInfo[]> {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));
  return requestClient.post<WorkflowFileInfo[]>(
    `${BASE_URL}/workflow-files/upload-batch`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
}

/**
 * 获取文件信息
 */
export function getWorkflowFileInfo(fileId: string) {
  return requestClient.get<WorkflowFileInfo>(
    `${BASE_URL}/workflow-files/${fileId}`,
  );
}

/**
 * 删除工作流文件
 */
export function deleteWorkflowFile(fileId: string) {
  return requestClient.delete<void>(`${BASE_URL}/workflow-files/${fileId}`);
}

// ==================== 工作流 API Key 管理 ====================

export interface ApiKeyCreateReq {
  workflowId: number | string;
  name: string;
  rateLimit?: number;
  expireDays?: number;
}

export interface ApiKeyCreateResp {
  id: number;
  apiKey: string;
  name: string;
  workflowId: number;
}

export interface ApiKeyListResp {
  id: number;
  name: string;
  apiKeyMasked: string;
  status: string;
  rateLimit: number;
  expireTime: null | string;
  lastUsedTime: null | string;
  totalCalls: number;
  createTime: null | string;
}

/**
 * 创建 API Key
 */
export function createApiKey(req: ApiKeyCreateReq): Promise<ApiKeyCreateResp> {
  return requestClient.post<ApiKeyCreateResp>(
    `${BASE_URL}/workflow-api-keys`,
    req,
  );
}

/**
 * 查询工作流的 API Key 列表
 */
export function listApiKeys(
  workflowId: number | string,
): Promise<ApiKeyListResp[]> {
  return requestClient.get<ApiKeyListResp[]>(
    `${BASE_URL}/workflow-api-keys/workflows/${workflowId}`,
  );
}

/**
 * 更新 API Key 状态
 */
export function updateApiKeyStatus(id: number, status: string): Promise<void> {
  return requestClient.put<void>(
    `${BASE_URL}/workflow-api-keys/${id}/status`,
    null,
    {
      params: { status },
    },
  );
}

/**
 * 删除 API Key
 */
export function deleteApiKey(id: number): Promise<void> {
  return requestClient.delete<void>(`${BASE_URL}/workflow-api-keys/${id}`);
}
