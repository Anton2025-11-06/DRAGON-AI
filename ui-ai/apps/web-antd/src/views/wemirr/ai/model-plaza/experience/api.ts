import { resolveApiUrl } from '#/api/helper';
import { getTraceId, saveTraceId } from '#/api/request';

import { GetMyKeys } from '../api';

export { GetMyKeys };

// ==================== 类型定义 ====================

/** 体验请求：按 12 能力类型构造的网关 body（对齐 common_model.entry.body_to_kwargs 字段） */
export interface ModelExperienceBody {
  prompt?: string;
  input?: string;
  messages?: { role: 'assistant' | 'user'; content: string }[];
  image_url?: string;
  video_url?: string;
  audio_url?: string;
  query?: string;
  documents?: string[];
  text?: string;
  voice?: string;
  stream?: boolean;
}

/** 解析后的统一体验结果（供 ResultPanel 渲染） */
export interface ModelExperienceResult {
  text: string;
  reasoning?: string;
  urls: string[];
  vectors: { dim: number; count: number; sample: number[] } | null;
  scores: { index: number; relevance_score: number }[] | null;
  usage: Record<string, any> | null;
  raw: any;
}

/** 体验请求错误：附带 HTTP 状态码与上游完整响应体 */
export class ModelExperienceError extends Error {
  responseData?: unknown;
  status?: number;

  constructor(
    message: string,
    opts: { responseData?: unknown; status?: number } = {},
  ) {
    super(message);
    this.name = 'ModelExperienceError';
    this.responseData = opts.responseData;
    this.status = opts.status;
  }
}

// ==================== 网关调用 ====================

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
 * - stream=true 时走 SSE 逐块回调 delta.content / delta.reasoning_content，结束回调 done=true
 * - 非流式：一次性回调完整内容（done=true）
 */
export async function invokeModel(
  req: {
    apiKey: string;
    model: string;
    body: ModelExperienceBody;
  },
  onChunk: (chunk: { content: string; reasoningContent: string; done: boolean }) => void,
  signal?: AbortSignal,
): Promise<{ text: string; raw: any; usage?: any }> {
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
  const url = resolveApiUrl('/api/model', baseUrl);
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
      body: JSON.stringify({ model: req.model, ...req.body }),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    throw new Error('无法连接模型，请检查网络后重试');
  }
  saveTraceId(resp.headers.get('x-trace-id') || '');

  // 非流式：一次性返回完整结果
  if (!req.body.stream) {
    const text = await resp.text();
    if (!resp.ok) {
      throw new ModelExperienceError(
        extractGatewayError(text) || `模型请求失败（${resp.status}）`,
        { responseData: parseResponseBody(text), status: resp.status },
      );
    }
    try {
      const data = JSON.parse(text);
      onChunk({ content: '', reasoningContent: '', done: true });
      return { text, raw: data };
    } catch {
      throw new Error('模型响应解析失败');
    }
  }

  // 流式：SSE 解析（data: {choices:[{delta:{...}}]} + [DONE]）
  if (!resp.ok) {
    const text = await resp.text();
    throw new ModelExperienceError(
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
  let usage: any = null;
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
          if (json?.usage) usage = json.usage;
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
  return { text: '', raw: null, usage };
}

// ==================== 响应解析 ====================

/** 把网关各类型响应解析为统一体验结果（对齐 result_to_openai 各分支；category 预留类型化扩展） */
export function parseExperienceResult(_category: string, data: any): ModelExperienceResult {
  const empty: ModelExperienceResult = {
    text: '',
    urls: [],
    vectors: null,
    scores: null,
    usage: null,
    raw: data,
  };
  if (!data || typeof data !== 'object') return empty;

  // chat 族：choices[0].message.content
  const choices = data.choices;
  if (Array.isArray(choices) && choices[0]?.message) {
    return {
      ...empty,
      text: choices[0].message.content || '',
      reasoning: choices[0].message.reasoning_content || undefined,
      usage: data.usage || null,
    };
  }

  // 向量类：data[i].embedding
  const dataArr = data.data;
  if (Array.isArray(dataArr) && dataArr[0] && Array.isArray(dataArr[0].embedding)) {
    const dim = dataArr[0].embedding.length;
    return {
      ...empty,
      vectors: {
        dim,
        count: dataArr.length,
        sample: dataArr[0].embedding.slice(0, 8).map((v: number) => Number(v.toFixed(4))),
      },
      usage: data.usage || null,
    };
  }

  // 重排：results[]
  if (Array.isArray(data.results)) {
    return { ...empty, scores: data.results };
  }

  // ASR：{task_id, text}
  if (typeof data.text === 'string' && data.text) {
    return { ...empty, text: data.text };
  }

  // 文生图：data[i].url
  const urls: string[] = [];
  if (Array.isArray(dataArr)) {
    for (const item of dataArr) {
      if (typeof item?.url === 'string' && item.url) urls.push(item.url);
    }
  }
  // 视频/音频：video_url / url（可能为 base64 data URI）
  if (typeof data.video_url === 'string' && data.video_url) urls.push(data.video_url);
  if (typeof data.url === 'string' && data.url) urls.push(data.url);
  if (urls.length > 0) return { ...empty, urls };
  return empty;
}