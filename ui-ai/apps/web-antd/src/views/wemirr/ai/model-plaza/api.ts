import type { ModelCategory, ModelProviderKey } from '#/api/ai-workflow/const';

import { useAccessStore } from '@vben/stores';

import { dict } from '@fast-crud/fast-crud';

import { resolveApiUrl } from '#/api/helper';
import { defHttp, getTraceId, saveTraceId } from '#/api/request';

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
  // 列表不返回 base_url/gateway_url/api_key：上游地址与管理端密钥属于凭据，只随详情下发给模型管理账号
  rate_limit_qps: number;
  status: boolean;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  /** 工具调用能力位（仅文生文）：决定工作流 LLM 节点能不能插入 MCP/工具/工作流 */
  supports_function_call?: boolean;
  stream_param?: null | string;
  thinking_param?: null | string;
  common_params?: CommonParam[];
  created_by: number;
  create_time: string;
  update_time: string;
  /** 当前用户申请状态：null 未申请 0 待审批 1 已通过 2 已拒绝 */
  apply_status?: null | number;
}

/**
 * 模型详情：凭据字段（base_url/gateway_url/api_key）仅对 ADMIN 或有模型新增/编辑权限的账号返回，
 * 故均为可选；普通用户拿不到也不该拿。
 */
export interface ModelDetailRep {
  id: number;
  name: string;
  category: ModelCategory;
  category_label: string;
  provider: ModelProvider;
  provider_label: string;
  model_name: string;
  base_url?: string;
  gateway_url?: string;
  api_key?: string;
  rate_limit_qps: number;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  supports_function_call?: boolean;
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
  supports_function_call?: boolean;
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

/** 连通性测试流式增量块（SSE chunk 帧） */
export interface ModelTestChunk {
  content: string;
  reasoningContent: string;
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
  /** 模型网关地址（用户调用入口，代替真实厂商地址） */
  gateway_url?: string;
  api_key: string;
  tutorial_md: string;
  rate_limit_qps: number;
  supports_stream?: boolean;
  supports_thinking?: boolean;
  supports_function_call?: boolean;
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
export const GetRegistry = (params?: {
  category?: string;
  provider?: string;
}) => defHttp.get<RegistryItem[]>(`${BASE_URL}/registry`, { params });

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

/**
 * 模型连通性测试（SSE 流式）：POST /models/test，逐帧解析 `data: {json}`。
 * - {"type":"chunk",...}：仅流式类型会产生，回调 onChunk 逐块累积 content/reasoning_content
 * - {"type":"result",...}：最终汇总帧（含 success/message/latency_ms/data），作为返回值
 * 鉴权：Authorization Bearer（登录 token，网关 TokenCheckMiddleware 校验）。
 * 后端 test 为异步生成器，非流式也统一走 SSE（只产出一条 result 帧）。
 */
export async function TestModelStream(
  data: ModelTestReq,
  onChunk?: (chunk: ModelTestChunk) => void,
  signal?: AbortSignal,
): Promise<ModelTestRep> {
  const accessStore = useAccessStore();
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
  const url = resolveApiUrl(`${BASE_URL}/test`, baseUrl);
  const traceId = getTraceId();

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessStore.accessToken || ''}`,
        'Content-Type': 'application/json',
        // 网关 forward_downstream 仅对 accept=text/event-stream 或 /subscribe 做流式转发，
        // 否则会把整个 SSE 响应缓冲到结束才下发（逐块实时失效），故显式声明 SSE
        Accept: 'text/event-stream',
        ...(traceId ? { 'X-Trace-Id': traceId } : {}),
      },
      body: JSON.stringify(data),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw new Error('无法连接模型，请检查网络后重试');
  }
  saveTraceId(resp.headers.get('x-trace-id') || '');

  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(
      `测试请求失败（${resp.status}）${text ? `：${text.slice(0, 200)}` : ''}`,
    );
  }
  if (!resp.body) {
    throw new Error('当前浏览器不支持流式读取');
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let final: ModelTestRep | null = null;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const raw = line.trim();
        if (!raw.startsWith('data:')) continue;
        const payload = raw.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;
        let frame: any;
        try {
          frame = JSON.parse(payload);
        } catch {
          // 忽略无法解析的分行
          continue;
        }
        if (frame?.type === 'chunk') {
          onChunk?.({
            content: frame.content || '',
            reasoningContent: frame.reasoning_content || '',
          });
        } else if (frame?.type === 'result' || frame?.success !== undefined) {
          final = {
            success: !!frame.success,
            message: frame.message || '',
            latency_ms: frame.latency_ms,
            data: frame.data,
          };
        }
      }
    }
  } finally {
    reader.releaseLock?.();
  }
  if (!final) {
    throw new Error('测试响应异常：未收到结果');
  }
  return final;
}

export const ApplyModel = (id: number, reason?: string) =>
  defHttp.post(`${BASE_URL}/${id}/apply`, { reason });

export const GetMyKeys = () => defHttp.get<MyKeyRep[]>(`${BASE_URL}/my-keys`);

// ==================== 文件上传（存储能力在 common_storage，入口为工作流文件接口） ====================

/** 上传返回：匿名可访问 URL（OSS 预签名 / 本地存储为下载接口地址）+ 有效期 */
export interface FileUploadRep {
  /** 存储文件名：{uuid}_{原始名}，下载/删除/判存在按它寻址 */
  fileName: string;
  /** 文件原始名称（带格式后缀） */
  name: string;
  size: number;
  /** 可直接传给大模型拉取的文件 URL */
  url: string;
  /** URL 有效期（秒） */
  expiresIn: number;
  /** URL 过期时间 */
  expiresAt: string;
}

/** 上传文件，返回 { fileName, url }；url 可直接传给大模型拉取 */
export const UploadFile = async (file: File): Promise<FileUploadRep> => {
  const fd = new FormData();
  fd.append('file', file);
  // defHttp 默认 JSON，此处需 multipart：走原生 fetch（不手设 Content-Type，交给浏览器带 boundary）。
  // 上传不在网关白名单内，必须显式带 Authorization；resolveApiUrl 用于去重前缀（VITE_GLOB_API_URL=/api
  // 时避免拼成 /api/api/...）
  const accessStore = useAccessStore();
  const base = import.meta.env.VITE_GLOB_API_URL || '';
  const url = resolveApiUrl('/api/workflow/workflow-files/upload', base);
  const resp = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessStore.accessToken || ''}` },
    body: fd,
  });
  const j = await resp.json();
  if (j?.code !== 200) throw new Error(j?.message || '上传失败');
  return j.data as FileUploadRep;
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
