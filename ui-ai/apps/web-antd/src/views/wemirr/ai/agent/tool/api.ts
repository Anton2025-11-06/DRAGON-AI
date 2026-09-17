import { defHttp } from '#/api/request';

const BASE_URL = '/api/workflow/tools';

export interface ToolPageReq {
  current: number;
  size: number;
  name?: string;
  status?: boolean;
}

/** 工具参数定义行（parameters_schema.parameters，工具页表单与节点表单共用口径） */
export interface ToolParamDef {
  default?: any;
  description?: string;
  name: string;
  required?: boolean;
  type?: string;
}

export interface ToolItem {
  id: number;
  name: string;
  description?: string;
  function_code?: string;
  parameters_schema?: string;
  /** 详情接口返回：已摊平的参数定义（列表接口无此字段） */
  parameters?: ToolParamDef[];
  status: boolean;
  /** 执行超时（毫秒），工具节点未单独配置时取此值 */
  timeout?: number;
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
  timeout?: number;
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

// 按定义测试：表单里未保存的代码直接跑一次（新增/编辑弹窗的「测试」）
export const TestDefinition = (
  functionCode: string,
  parameters: Record<string, any>,
  timeout?: number,
) =>
  defHttp.post<ToolTestResult>(`${BASE_URL}/test-definition`, {
    function_code: functionCode,
    parameters,
    timeout,
  });

/** 工具下拉项：节点表单据此自动列出参数绑定行 */
export interface ToolOption {
  description?: string;
  id: number;
  name: string;
  parameters: ToolParamDef[];
  timeout: number;
}

// 启用中工具下拉（含参数定义），供工作流工具节点选工具
export const GetOptions = () =>
  defHttp.get<ToolOption[]>(`${BASE_URL}/options`);
