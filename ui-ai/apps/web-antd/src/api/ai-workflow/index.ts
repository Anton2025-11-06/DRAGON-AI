/**
 * AI 工作流 API
 * 提供工作流定义、执行、模板管理的 API 接口
 */

import type {
  AiModelOption,
  DynamicToolOption,
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
  WorkflowSubmitReq,
  WorkflowTemplatePageReq,
  WorkflowTemplateResp,
  WorkflowTemplateSaveReq,
  WorkflowVersionResp,
} from './types';

import type { PageResult } from '#/api/common';

import { resolveApiUrl } from '#/api/helper';
import { requestClient } from '#/api/request';

import { MODEL_TYPE_TEXT } from './const';

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

/** 模型下拉（type 取值见 ./const 的 MODEL_TYPE_*） */
export function listAiModels(type = MODEL_TYPE_TEXT) {
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

/**
 * 工作流 MCP 节点的连接下拉：只取「启用」状态的连接
 * 后端 POST /mcp-server/page 返回 data = { total, items: [...] }（经 requestClient 剥壳后
 * 直接是 items 分页体），故解析时以 items 为准，兼容 records/裸数组等历史形态。
 */
export function listMcpServers() {
  return requestClient
    .post<
      | McpServerOption[]
      | { items?: McpServerOption[]; records?: McpServerOption[] }
    >(`${BASE_URL}/mcp-server/page`, { current: 1, size: 100, status: true })
    .then((resp) =>
      Array.isArray(resp) ? resp : resp.items || resp.records || [],
    );
}

export function listMcpServerTools(serverId: number | string) {
  return requestClient.get<McpToolOption[]>(
    `${BASE_URL}/mcp-server/${serverId}/tools`,
  );
}

/**
 * 动态函数工具下拉（工具库中启用中的 Python 函数工具）
 * 返回项带 parameters 参数定义，工作流工具节点据此生成参数绑定行
 */
export function listDynamicTools() {
  return requestClient.get<DynamicToolOption[]>(`${BASE_URL}/tools/options`);
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
 * 异步执行工作流（需求 4:后台已取消同步 /execute,统一 execute-async 投递）
 * 返回 executionId,状态/结果通过 SSE 订阅或 GET 执行详情轮询获取。
 * 传了 apiKey 就加 X-Workflow-Token 请求头，走网关的 API Key 模式（Redis 校验 Key
 * + 按 Key 配置的 QPS 限流）；不传走登录态直转。
 */
export function executeWorkflowAsync(
  workflowId: number | string,
  data: WorkflowExecutionReq,
  apiKey?: string,
) {
  return requestClient.post<string>(
    `${BASE_URL}/workflow-executions/workflows/${workflowId}/execute-async`,
    data,
    { headers: apiKey ? { 'X-Workflow-Token': apiKey } : undefined },
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
 * 取消执行
 * 引擎在下一个节点检查点生效；PAUSED（审批挂起）下直接写终态。
 * 也是「上次任务未结束」时的解套手段（提交接口的提示文案就引导到这里）。
 */
export function cancelExecution(executionId: string, apiKey?: string) {
  return requestClient.post<void>(
    `${BASE_URL}/workflow-executions/${executionId}/cancel`,
    null,
    { headers: apiKey ? { 'X-Workflow-Token': apiKey } : undefined },
  );
}

/**
 * 再提交（需求 3：指定 executionId 的重新执行 / 审批后恢复，异步）
 * mode=RETRY 全量重跑（终态可用）；mode=CONTINUE 审批后恢复（仅 PAUSED，必带 approval）。
 * 校验失败统一返回 400 + 可读文案（未结束 / 画布漂移 / 无审批权限 / 结论不全）。
 * @param executionId 目标执行 id（同时充当会话 id）
 * @param data 提交体：mode + 可选 inputs / approval 结论
 * @param apiKey 第三方审批方无登录态时传，走 X-Workflow-Token（后端校 key ↔ 工作流归属）
 */
export function submitExecution(
  executionId: string,
  data: WorkflowSubmitReq,
  apiKey?: string,
) {
  return requestClient.post<{
    executionId: string;
    id: string;
    mode: string;
    status: string;
  }>(`${BASE_URL}/workflow-executions/${executionId}/submit`, data, {
    headers: apiKey ? { 'X-Workflow-Token': apiKey } : undefined,
  });
}

// ==================== 调试控制 API ====================

/**
 * 获取执行快照（只读，排障与详情展示）
 * 快照不再是恢复输入：权威源是 node_states，恢复入口只有 submit
 */
export function getExecutionSnapshot(executionId: string) {
  return requestClient.get<Record<string, any>>(
    `${BASE_URL}/workflow-executions/${executionId}/snapshot`,
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

// ==================== WebSocket 同步执行 ====================

/**
 * http(s) 绝对地址 → ws(s)；相对路径按当前站点协议 + host 补全
 */
function toWsUrl(path: string): string {
  if (/^https:\/\//i.test(path)) return path.replace(/^https:/i, 'wss:');
  if (/^http:\/\//i.test(path)) return path.replace(/^http:/i, 'ws:');
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}${path}`;
}

/**
 * 同步执行 WebSocket 地址（预览运行专用）：建立连接后即触发执行，
 * 事件流（node.started / node.delta / workflow.completed 等）经该 WS 实时回推，
 * 替代原「execute-async + SSE 订阅 Redis」链路。
 * @param workflowId 工作流ID
 * @param baseUrl 基础地址（如 VITE_GLOB_API_URL=/api）
 */
export function getWorkflowExecuteSyncWsUrl(
  workflowId: number | string,
  baseUrl = '',
): string {
  return toWsUrl(
    resolveApiUrl(
      `${BASE_URL}/workflow-executions/workflows/${workflowId}/execute-sync`,
      baseUrl,
    ),
  );
}

/**
 * 同步再提交 WebSocket 地址（预览页审批入口）：首帧带 mode/inputs/approval，
 * 与 execute-sync 同一套握手与事件回推口径（审批人点「同意/不同意」后继续跑完剩余节点）。
 * @param executionId 执行ID
 * @param baseUrl 基础地址（如 VITE_GLOB_API_URL=/api）
 */
export function getWorkflowSubmitSyncWsUrl(
  executionId: string,
  baseUrl = '',
): string {
  return toWsUrl(
    resolveApiUrl(
      `${BASE_URL}/workflow-executions/${executionId}/submit-sync`,
      baseUrl,
    ),
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
 * 文件上传结果（common_storage 口径：文件名即唯一标识，不再单独维护 fileId）
 */
export interface WorkflowFileUpload {
  /** 是否上传成功（批量上传时逐项判定） */
  ok: boolean;
  /** 存储文件名：{uuid}_{原始名}，下载/删除/判存在都用它 */
  fileName: string;
  /** 文件原始名称（带格式后缀） */
  name: string;
  /** 文件大小（字节） */
  size: number;
  /** 匿名可访问 URL（OSS 为预签名地址，本地存储为下载接口地址） */
  url: string;
  /** URL 有效期（秒） */
  expiresIn: number;
  /** URL 过期时间 */
  expiresAt: string;
  /** 失败原因（ok 为 false 时） */
  error?: string;
}

/**
 * 上传单个文件（开始节点文件类型参数、文档提取器入参均先由此拿到 url）
 */
export function uploadWorkflowFile(file: File): Promise<WorkflowFileUpload> {
  const formData = new FormData();
  formData.append('file', file);
  return requestClient.post<WorkflowFileUpload>(
    `${BASE_URL}/workflow-files/upload`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
}

/**
 * 批量上传文件（逐项返回成败，不会因为一个失败丢掉整批）
 */
export function uploadWorkflowFiles(
  files: File[],
): Promise<WorkflowFileUpload[]> {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));
  return requestClient.post<WorkflowFileUpload[]>(
    `${BASE_URL}/workflow-files/upload-batch`,
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
}

/**
 * 匿名下载地址（已在网关白名单内，可直接用于 a[href] / window.open）
 */
export function getWorkflowFileDownloadUrl(fileName: string): string {
  return resolveApiUrl(
    `${BASE_URL}/workflow-files/download/${encodeURIComponent(fileName)}`,
  );
}

/**
 * 判断文件是否存在（过期视为不存在）
 */
export function existsWorkflowFile(fileName: string) {
  return requestClient.get<boolean>(
    `${BASE_URL}/workflow-files/exists/${encodeURIComponent(fileName)}`,
  );
}

/**
 * 删除工作流文件
 */
export function deleteWorkflowFile(fileName: string) {
  return requestClient.delete<void>(
    `${BASE_URL}/workflow-files/${encodeURIComponent(fileName)}`,
  );
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

/**
 * 编辑 API Key：只传要改的字段
 *
 * expireTime 传 null 是「改为永不过期」，不传是「不改」，两者语义不同。
 */
export interface ApiKeyUpdateReq {
  name?: string;
  rateLimit?: number;
  expireTime?: null | string;
}

export interface ApiKeyListResp {
  id: number;
  name: string;
  /** 完整 API Key（需求：不做脱敏，可随时查看） */
  apiKey: string;
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
 * 编辑 API Key（重命名 / 改 QPS / 改过期时间）
 */
export function updateApiKey(id: number, req: ApiKeyUpdateReq): Promise<void> {
  return requestClient.put<void>(`${BASE_URL}/workflow-api-keys/${id}`, req);
}

/**
 * 删除 API Key
 */
export function deleteApiKey(id: number): Promise<void> {
  return requestClient.delete<void>(`${BASE_URL}/workflow-api-keys/${id}`);
}
