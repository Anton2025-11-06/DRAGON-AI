import { dict } from '@fast-crud/fast-crud';

import { resolveApiUrl } from '#/api/helper';
import { defHttp } from '#/api/request';

import type {
  ModelCategory,
  ModelProviderKey,
} from '#/api/ai-workflow/const';

// ==================== 类型定义 ====================

// 模型分类（统一常量入口：ModelCategory 来自 #/api/ai-workflow/const，12 能力类型 code）

/** 模型提供商（common_model 实现的 3 家） */
export type ModelProvider = ModelProviderKey;

/** 常用参数类型枚举（需求 2.3.4） */
export type CommonParamType =
  | 'boolean'
  | 'integer'
  | 'number'
  | 'object'
  | 'string';

/** 常用参数条目（底层 JSON 存储，需求 2.3） */
export interface CommonParam {
  name: string;
  default?: any;
  desc?: string;
  type: CommonParamType;
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
  rate_limit_qps: number;
  status: boolean;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  stream_param?: null | string;
  thinking_param?: null | string;
  common_params?: CommonParam[];
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
  api_key?: string;
  rate_limit_qps: number;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  stream_param?: null | string;
  thinking_param?: null | string;
  common_params?: CommonParam[];
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
  api_key: string;
  rate_limit_qps: number;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  stream_param?: string;
  thinking_param?: string;
  common_params?: CommonParam[];
  tutorial_md: string;
  status?: boolean;
}

/** 模型测试请求（走 common_model 按 (类型, 供应商) 真实调用，支持输入/流式/思考/常用参数） */
export interface ModelTestReq {
  category: ModelCategory;
  provider: ModelProvider;
  model_name: string;
  base_url?: string;
  api_key?: string;
  inputs?: Record<string, any>;
  stream?: boolean;
  thinking?: boolean;
  params?: Record<string, any>;
}

/** 连通性测试结果 */
export interface ModelTestRep {
  success: boolean;
  message: string;
  latency_ms?: number;
  /** 上游真实返回的样例数据（探测响应原文，供结果弹窗展示） */
  data?: any;
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
  api_key: string;
  tutorial_md: string;
  rate_limit_qps: number;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  stream_param?: null | string;
  thinking_param?: null | string;
  common_params?: CommonParam[];
  apply_time: string;
}

/** 分类/提供商字典 */
export interface ModelDictRep {
  categories: { label: string; value: string }[];
  providers: { label: string; value: string }[];
}

/** 模型标识注册表条目（全部经真实 API 调用验证，见后端 common_constants/model_registry.py） */
export interface RegistryItem {
  provider: ModelProvider;
  provider_label: string;
  category: ModelCategory;
  category_label: string;
  model_name: string;
}

// ==================== API 接口 ====================

// 模型广场接口由 service_system 提供（RBAC 权限 system:model:*）
const BASE_URL = '/api/system/models';

export const PageList = (params: ModelPageReq) =>
  defHttp.post<ModelPageRep[]>(`${BASE_URL}/page`, params);

export const GetCategories = () =>
  defHttp.get<ModelDictRep>(`${BASE_URL}/categories`);

/** 模型标识注册表：按 厂家/类型 过滤（不传返回全部） */
export const GetRegistry = (params?: { provider?: string; category?: string }) =>
  defHttp.get<RegistryItem[]>(`${BASE_URL}/registry`, { params });

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
  // 模型连通性测试会真实调用上游（含生成类异步任务轮询），默认 10s 易超时，单独延长到 60s
  defHttp.post<ModelTestRep>(`${BASE_URL}/test`, data, { timeout: 60_000 });

export const ApplyModel = (id: number, reason?: string) =>
  defHttp.post(`${BASE_URL}/${id}/apply`, { reason });

export const GetMyKeys = () => defHttp.get<MyKeyRep[]>(`${BASE_URL}/my-keys`);

// ==================== 文件上传（service_file，无鉴权） ====================

/** 上传返回：内网可访问的可下载 URL（公网转发由运维处理） */
export interface FileUploadRep {
  fileId: string;
  name: string;
  size: number;
  url: string;
}

/** 上传文件，返回 { fileId, url }；url 可直接传给大模型拉取 */
export const UploadFile = (file: File): Promise<FileUploadRep> => {
  const fd = new FormData();
  fd.append('file', file);
  // defHttp 默认 JSON，此处需 multipart：走原生 fetch 到网关 /api/file/file/upload
  // 用 resolveApiUrl 去重前缀（VITE_GLOB_API_URL=/api 时避免拼成 /api/api/...）
  const base = import.meta.env.VITE_GLOB_API_URL || '';
  const url = resolveApiUrl('/api/file/file/upload', base);
  return fetch(url, { method: 'POST', body: fd })
    .then(async (r) => {
      const j = await r.json();
      if (j?.code !== 200) throw new Error(j?.message || '上传失败');
      return j.data as FileUploadRep;
    });
};

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
