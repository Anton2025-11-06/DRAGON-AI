import { useAccessStore } from '@vben/stores';

import { resolveApiUrl } from '#/api/helper';
import { getTraceId, saveTraceId } from '#/api/request';

// ==================== 类型定义 ====================

/** 模型对话-会话 */
export interface ChatSessionRep {
  id: number;
  title: string;
  model_apply_id: number;
  model_name: null | string;
  reasoning: boolean;
  stream: boolean;
  create_time: string;
  update_time: string;
}

/** 模型对话-新建会话 */
export interface ChatSessionCreateReq {
  title?: string;
  model_apply_id?: number;
  model_name?: string;
  reasoning?: boolean;
  stream?: boolean;
}

/** 模型对话-更新会话 */
export interface ChatSessionUpdateReq {
  title?: string;
  model_apply_id?: number;
  model_name?: null | string;
  reasoning?: boolean;
  stream?: boolean;
}

/** 模型对话-会话消息 */
export interface ChatMessageRep {
  id: number;
  role: 'ASSISTANT' | 'USER';
  content: string;
  reasoning_content: string;
  model_name: null | string;
  create_time: string;
}

/** 模型对话-保存消息 */
export interface ChatMessageSaveItem {
  role: 'ASSISTANT' | 'USER';
  content?: string;
  reasoning_content?: string;
  model_name?: string;
}

/** 模型对话-发送给网关的消息 */
export interface ModelChatMsg {
  role: 'assistant' | 'user';
  content: string;
}

/** 模型对话-网关请求参数 */
export interface ModelChatReq {
  apiKey: string;
  messages: ModelChatMsg[];
  model: string;
  /** 是否直连：直连调 /api/model；非直连调 /api/model{后缀} */
  isDirect: boolean;
  /** 非直连模型选中的接口后缀，如 /v1/chat/completions */
  suffix?: string;
  reasoning?: boolean;
  stream: boolean;
  search: boolean;
}

/** 模型对话-增量输出（流式逐块 / 非流式一次全量） */
export interface ModelChatChunk {
  content: string;
  done: boolean;
  reasoningContent: string;
}

// ==================== 会话/消息 CRUD（登录 token 鉴权，Authorization 头直连网关 /api/workflow） ====================

// 会话/消息与对话同源：会话经网关正常 token 认证（Authorization 头），按 user_id 强隔离
const BASE_URL = '/api/workflow/sessions';

/** 统一网关业务请求：成功体 {code:200,message,data}，失败体 {detail} / {message}；
 * 鉴权：请求头 Authorization 携带登录 token（网关 TokenCheckMiddleware 校验后注入 X-User-Token 下发） */
async function sessionFetch<T>(
  method: 'DELETE' | 'GET' | 'POST' | 'PUT',
  path: string,
  body?: unknown,
): Promise<T> {
  const accessStore = useAccessStore();
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
  const url = resolveApiUrl(`${BASE_URL}${path}`, baseUrl);
  // 后端响应携带过 X-Trace-Id 后回带同一 trace-id，串联同一用户行为
  const traceId = getTraceId();
  let resp: Response;
  try {
    resp = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${accessStore.accessToken || ''}`,
        'Content-Type': 'application/json',
        ...(traceId ? { 'X-Trace-Id': traceId } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('无法连接模型网关，请检查网络后重试');
  }
  // 响应头携带链路 ID（成功/失败均有），记录供后续请求回带
  saveTraceId(resp.headers.get('x-trace-id') || '');
  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(extractGatewayError(text) || `请求失败（${resp.status}）`);
  }
  let json: Record<string, any> = {};
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error('网关响应解析失败');
  }
  if (json.code !== 200) {
    throw new Error(json.message || '请求失败');
  }
  return json.data as T;
}

export const getSessions = () => sessionFetch<ChatSessionRep[]>('GET', '');

export const createSession = (data: ChatSessionCreateReq) =>
  sessionFetch<{ id: number }>('POST', '', data);

export const updateSession = (id: number, data: ChatSessionUpdateReq) =>
  sessionFetch<null>('PUT', `/${id}`, data);

export const deleteSession = (id: number) =>
  sessionFetch<null>('DELETE', `/${id}`);

export const getSessionMessages = (id: number) =>
  sessionFetch<ChatMessageRep[]>('GET', `/${id}/messages`);

export const saveSessionMessages = (
  id: number,
  messages: ChatMessageSaveItem[],
) => sessionFetch<null>('POST', `/${id}/messages`, { messages });

// ==================== 对话请求（api-key 鉴权，X-User-Api-Key 头直连网关 /api/model） ====================

/** 模型对话请求错误：附带 HTTP 状态码与上游完整响应体（页面展示接口数据用） */
export class ModelChatError extends Error {
  responseData?: unknown;
  status?: number;

  constructor(
    message: string,
    opts: { responseData?: unknown; status?: number } = {},
  ) {
    super(message);
    this.name = 'ModelChatError';
    this.responseData = opts.responseData;
    this.status = opts.status;
  }
}

/** 从 OpenAI 风格错误体提取文案 */
function extractGatewayError(text: string): string {
  try {
    const data = JSON.parse(text);
    if (data?.error?.message) return String(data.error.message);
    if (typeof data?.message === 'string') return data.message;
    if (typeof data?.detail === 'string') return data.detail;
  } catch {
    // 非 JSON 响应体，返回空串由调用方兜底
  }
  return '';
}

/** 解析接口响应体供页面展示：JSON 解析失败（如 HTML 错误页）降级为原始文本 */
function parseResponseBody(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * 调用网关 /api/model（OpenAI 兼容，body 原样透传）
 * - 鉴权：X-User-Api-Key: mk_xxx（已授权模型密钥）
 * - 流式：SSE 逐块回调 delta.content / delta.reasoning_content，结束回调 done=true
 * - 非流式：一次性回调完整内容（done=true）
 * - 深度思考：透传顶层 reasoning: true
 */
export async function chatWithModel(
  req: ModelChatReq,
  onChunk: (chunk: ModelChatChunk) => void,
  signal?: AbortSignal,
): Promise<void> {
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
  // 直连：/api/model（模型真实地址已是完整路径）；非直连：/api/model + 接口后缀（base_url + 后缀转发）
  const endpoint = req.isDirect
    ? '/api/model'
    : `/api/model${req.suffix || ''}`;
  const url = resolveApiUrl(endpoint, baseUrl);

  const payload: Record<string, any> = {
    model: req.model,
    messages: req.messages,
    stream: req.stream,
    enable_thinking: req.reasoning,
    enable_search: req.search,
  };

  const traceId = getTraceId();
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: {
        'X-User-Api-Key': req.apiKey,
        'Content-Type': 'application/json',
        ...(traceId ? { 'X-Trace-Id': traceId } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw new Error('无法连接模型，请检查网络后重试');
  }
  // 响应头携带链路 ID（SSE 流同样在响应头），记录供后续请求回带
  saveTraceId(resp.headers.get('x-trace-id') || '');

  // 非流式：一次性返回完整结果
  if (!req.stream) {
    const text = await resp.text();
    if (!resp.ok) {
      throw new ModelChatError(
        extractGatewayError(text) || `模型请求失败（${resp.status}）`,
        { responseData: parseResponseBody(text), status: resp.status },
      );
    }
    try {
      const data = JSON.parse(text);
      const msg = data?.choices?.[0]?.message || {};
      onChunk({
        content: msg.content || '',
        reasoningContent: msg.reasoning_content || '',
        done: true,
      });
    } catch {
      throw new Error('模型响应解析失败');
    }
    return;
  }

  // 流式：SSE 解析（data: {choices:[{delta:{...}}]} + [DONE]）
  if (!resp.ok) {
    const text = await resp.text();
    throw new ModelChatError(
      extractGatewayError(text) || `模型请求失败（${resp.status}）`,
      { responseData: parseResponseBody(text), status: resp.status },
    );
  }
  if (!resp.body) {
    throw new Error('当前浏览器不支持流式读取');
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const data = line.trim();
        if (!data.startsWith('data:')) continue;
        const raw = data.slice(5).trim();
        if (!raw || raw === '[DONE]') continue;
        try {
          const json = JSON.parse(raw);
          const delta = json?.choices?.[0]?.delta || {};
          if (delta.content || delta.reasoning_content) {
            onChunk({
              content: delta.content || '',
              reasoningContent: delta.reasoning_content || '',
              done: false,
            });
          }
        } catch {
          // 忽略无法解析的分行
        }
      }
    }
  } finally {
    reader.releaseLock?.();
  }
  onChunk({ content: '', reasoningContent: '', done: true });
}
