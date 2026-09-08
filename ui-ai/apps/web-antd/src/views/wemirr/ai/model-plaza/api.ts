import { dict } from '@fast-crud/fast-crud';

import { defHttp } from '#/api/request';

import type { ModelCategory } from '#/api/ai-workflow/const';

// ==================== 类型定义 ====================

// 模型分类（统一常量入口：ModelCategory 来自 #/api/ai-workflow/const）

/** 模型提供商 */
export type ModelProvider =
  | 'deepseek'
  | 'doubao'
  | 'hunyuan'
  | 'kimi'
  | 'openai'
  | 'qwen';

/** 非直连模型的接口后缀：后缀 URI + 接口能力说明 */
export interface ModelSuffix {
  url: string;
  desc?: string;
}

/** 非直连模型的接口后缀：后缀 URI + 接口能力说明 */
export interface ModelSuffix {
  url: string;
  desc?: string;
}

/** 分页查询参数 */
export interface ModelPageReq {
  current: number;
  size: number;
  name?: string;
  category?: string;
  provider?: string;
  status?: boolean;
}

/** 列表项（含当前用户申请状态） */
export interface ModelPageRep {
  id: number;
  name: string;
  category: ModelCategory;
  category_label: string;
  provider: ModelProvider;
  provider_label: string;
  model_name: string;
  base_url: string;
  /** 模型网关地址（展示给调用方） */
  gateway_url?: string;
  /** 是否直连：true=base_url 含完整接口路径，false=base_url+接口后缀 */
  is_direct?: boolean;
  /** 非直连时支持的后缀列表 */
  suffixes?: ModelSuffix[];
  rate_limit_qps: number;
  status: boolean;
  created_by: number;
  create_time: string;
  update_time: string;
  /** 当前用户申请状态：null 未申请 0 待审批 1 已通过 2 已拒绝 */
  apply_status?: null | number;
}

/** 模型详情（管理员含 api_key） */
export interface ModelDetailRep {
  id: number;
  name: string;
  category: ModelCategory;
  category_label: string;
  provider: ModelProvider;
  provider_label: string;
  model_name: string;
  base_url: string;
  gateway_url?: string;
  is_direct?: boolean;
  suffixes?: ModelSuffix[];
  api_key?: string;
  rate_limit_qps: number;
  tutorial_md: string;
  status: boolean;
}

/** 保存参数 */
export interface ModelSaveReq {
  name: string;
  category: ModelCategory;
  provider: ModelProvider;
  model_name: string;
  base_url: string;
  gateway_url?: string;
  is_direct?: boolean;
  suffixes?: ModelSuffix[];
  api_key?: string;
  rate_limit_qps: number;
  tutorial_md: string;
  status?: boolean;
}

/** 连通性测试请求（走 OpenAI 兼容探测端点） */
export interface ModelTestReq {
  category: ModelCategory;
  model_name: string;
  base_url?: string;
  api_key?: string;
  /** 非直连模型的后缀 URI（拼接后探测） */
  suffix_url?: string;
}

/** 连通性测试结果 */
export interface ModelTestRep {
  success: boolean;
  message: string;
  latency_ms?: number;
}

/** 我的 API Key */
export interface MyKeyRep {
  apply_id: number;
  model_id: number;
  name: string;
  category: ModelCategory;
  category_label: string;
  provider: ModelProvider;
  provider_label: string;
  model_name: string;
  base_url: string;
  /** 模型网关地址（展示用，替代真实地址） */
  gateway_url?: string;
  /** 是否直连 */
  is_direct?: boolean;
  /** 非直连时支持的后缀 URI + 能力说明 */
  suffixes?: ModelSuffix[];
  api_key: string;
  tutorial_md: string;
  rate_limit_qps: number;
  apply_time: string;
}

/** 分类/提供商字典 */
export interface ModelDictRep {
  categories: { label: string; value: string }[];
  providers: { label: string; value: string }[];
}

// ==================== API 接口 ====================

// 模型广场接口由 service_system 提供（RBAC 权限 system:model:*）
const BASE_URL = '/api/system/models';

export const PageList = (params: ModelPageReq) =>
  defHttp.post<ModelPageRep[]>(`${BASE_URL}/page`, params);

export const GetCategories = () =>
  defHttp.get<ModelDictRep>(`${BASE_URL}/categories`);

export const GetDetail = (id: number) =>
  defHttp.get<ModelDetailRep>(`${BASE_URL}/${id}/detail`);

export const AddObj = (data: ModelSaveReq) =>
  defHttp.post(`${BASE_URL}/create`, data);

export const UpdateObj = (id: number, data: ModelSaveReq) =>
  defHttp.put(`${BASE_URL}/${id}/modify`, data);

export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

// defHttp 无 patch 方法（RequestClient 仅 delete/get/post/put），
// 状态切换改用通用 request + method: 'PATCH'（对应后端 PATCH /models/{id}/status）
export const ToggleStatus = (id: number, status: boolean) =>
  defHttp.request(`${BASE_URL}/${id}/status?status=${status}`, {
    method: 'PATCH',
  });

export const TestModel = (data: ModelTestReq) =>
  defHttp.post<ModelTestRep>(`${BASE_URL}/test`, data);

export const ApplyModel = (id: number, reason?: string) =>
  defHttp.post(`${BASE_URL}/${id}/apply`, { reason });

export const GetMyKeys = () => defHttp.get<MyKeyRep[]>(`${BASE_URL}/my-keys`);

// ==================== 审批 ====================

export interface ModelApplyPageRep {
  id: number;
  model_id: number;
  model_name: string;
  user_id: number;
  username: string;
  dept_id: number;
  reason: null | string;
  status: number;
  status_label: string;
  api_key: null | string;
  reject_reason: null | string;
  audit_by: null | string;
  audit_time: null | string;
  apply_time: string;
}

export const AppliesPage = (params: any) =>
  defHttp.post<ModelApplyPageRep[]>(`${BASE_URL}/applies/page`, params);

/** 审批列表状态字典（仅列/搜索展示） */
export const applyStatusDictForAudit = () =>
  dict({
    data: [
      { value: 0, label: '待审批', color: 'processing' },
      { value: 1, label: '已通过', color: 'success' },
      { value: 2, label: '已拒绝', color: 'error' },
    ],
  });

export const AuditApply = (
  id: number,
  approve: boolean,
  rejectReason?: string,
) =>
  defHttp.post(`${BASE_URL}/applies/${id}/audit`, {
    approve,
    reject_reason: rejectReason,
  });
