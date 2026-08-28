import { defHttp } from '#/api/request';

export interface McpServerConfigPageReq {
  current: number;
  size: number;
  column?: string;
  asc?: boolean;
  name?: string;
  status?: boolean;
}

export interface McpServerConfig {
  id: number;
  name: string;
  type: string; // STDIO/SSE
  command?: string;
  args?: string; // JSON字符串
  url?: string;
  env?: string; // JSON字符串
  status: boolean;
  createdTime?: string;
  updatedTime?: string;
}

export interface McpServerConfigSaveReq {
  name: string;
  type: string;
  command?: string;
  args?: string;
  url?: string;
  env?: string;
  status?: boolean;
}

export interface McpConnectionTestResult {
  success: boolean;
  errorMessage?: string;
  serverName?: string;
  toolCount?: number;
  responseTime?: number;
}

export interface McpToolInfo {
  name: string;
  description?: string;
}

// 分页查询
export const PageList = (params: McpServerConfigPageReq) =>
  defHttp.post<{ records: McpServerConfig[]; total: number }>(
    '/api/workflow/mcp-server/page',
    params,
  );

// 新增配置
export const AddObj = (data: McpServerConfigSaveReq) =>
  defHttp.post('/api/workflow/mcp-server/create', data);

// 修改配置
export const UpdateObj = (id: number, data: McpServerConfigSaveReq) =>
  defHttp.put(`/api/workflow/mcp-server/${id}/modify`, data);

// 删除配置
export const DelObj = (id: number) =>
  defHttp.delete(`/api/workflow/mcp-server/${id}`);

// 刷新连接
export const RefreshConnection = (id: number) =>
  defHttp.request(`/api/workflow/mcp-server/${id}/refresh`, {
    method: 'PATCH',
  });

// 测试连接（按ID）
export const TestConnection = (id: number) =>
  defHttp.post<McpConnectionTestResult>(
    `/api/workflow/mcp-server/${id}/test-connection`,
  );

// 测试连接（按当前表单参数，添加/编辑弹窗内“测试”按钮使用）
export const TestParams = (data: Partial<McpServerConfigSaveReq>) =>
  defHttp.post<McpConnectionTestResult>(
    '/api/workflow/mcp-server/test-params',
    data,
  );

// 获取工具列表
export const GetTools = (id: number) =>
  defHttp.get<McpToolInfo[]>(`/api/workflow/mcp-server/${id}/tools`);

// 切换启用状态
export const ToggleStatus = (id: number, status: boolean) =>
  defHttp.request(`/api/workflow/mcp-server/${id}/status`, {
    method: 'PATCH',
    params: { status },
  });
