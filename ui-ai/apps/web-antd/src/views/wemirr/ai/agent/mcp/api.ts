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
  description?: string;
  /** 非创建人且非管理员时后端隐藏 SSE url（url 为空且此标记为真） */
  urlHidden?: boolean;
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
  description?: string;
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
  /** MCP 工具输入参数 JSON Schema（tools/list 返回，仅展示） */
  inputSchema?: Record<string, any>;
}

/** 工具调用结果（与后端 _tool_result_to_dict 一致） */
export interface McpToolCallResult {
  /** 文本/资源内容拼接结果 */
  content: string;
  /** 图片/音频等二进制转 data URI 或资源 URI */
  urls: string[];
  isError: boolean;
  /** 结构化输出（部分工具给 JSON） */
  structured?: null | Record<string, any>;
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

// 调用工具（查看工具页的「测试」按钮）
export const CallTool = (
  id: number,
  toolName: string,
  args: Record<string, any>,
) =>
  defHttp.post<McpToolCallResult>(`/api/workflow/mcp-server/${id}/call-tool`, {
    tool_name: toolName,
    arguments: args,
  });

// 切换启用状态
export const ToggleStatus = (id: number, status: boolean) =>
  defHttp.request(`/api/workflow/mcp-server/${id}/status`, {
    method: 'PATCH',
    params: { status },
  });
