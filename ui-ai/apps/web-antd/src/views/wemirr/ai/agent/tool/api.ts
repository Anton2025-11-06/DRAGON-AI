import { defHttp } from '#/api/request';

const BASE_URL = '/api/workflow/tools';

export interface ToolPageReq {
  current: number;
  size: number;
  name?: string;
  status?: boolean;
}

export interface ToolItem {
  id: number;
  name: string;
  description?: string;
  function_code?: string;
  parameters_schema?: string;
  status: boolean;
  created_by?: number;
  create_time?: string;
  update_time?: string;
}

export interface ToolSaveReq {
  name: string;
  description?: string;
  function_code: string;
  parameters_schema?: string;
  status?: boolean;
}

export interface ToolTestResult {
  success: boolean;
  result?: any;
  error?: string;
  duration_ms?: number;
}

// 分页查询
export const PageList = (params: ToolPageReq) =>
  defHttp.get<{ records: ToolItem[]; total: number }>(`${BASE_URL}/page`, {
    params,
  });

// 详情（完整源码）
export const GetDetail = (id: number) =>
  defHttp.get<ToolItem>(`${BASE_URL}/${id}/detail`);

// 新增
export const AddObj = (data: ToolSaveReq) =>
  defHttp.post(`${BASE_URL}/create`, data);

// 修改
export const UpdateObj = (id: number, data: ToolSaveReq) =>
  defHttp.put(`${BASE_URL}/${id}/modify`, data);

// 删除
export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

// 切换启用状态
export const ToggleStatus = (id: number, status: boolean) =>
  defHttp.request(`${BASE_URL}/${id}/status`, {
    method: 'PATCH',
    params: { status },
  });

// 运行测试
export const TestTool = (id: number, parameters: Record<string, any>) =>
  defHttp.post<ToolTestResult>(`${BASE_URL}/${id}/test`, {
    parameters,
  });
