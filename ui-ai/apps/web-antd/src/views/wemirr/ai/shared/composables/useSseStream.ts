/**
 * SSE 流式请求 Composable
 * 统一处理 AI 对话的流式响应逻辑
 */
import { ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { SSE } from 'sse.js';

import { resolveApiUrl } from '#/api/helper';

import {
  applySseMessage,
  createStreamState,
  type ChatReference,
} from './stream-events';

// ==================== 类型定义 ====================

export interface SseStreamOptions {
  /** 请求 URL（不含 baseUrl） */
  url: string;
  /** 请求参数 */
  payload: Record<string, any>;
}

export interface SseStreamResult {
  /** 流式内容 */
  content: string;
  /** 引用来源 */
  references: ChatReference[];
  /** 思考过程 */
  thinking: string;
}

// ==================== Composable ====================

export function useSseStream() {
  // 流式输出状态
  const streamingContent = ref('');
  const streamingThinking = ref('');
  const streamingReferences = ref<ChatReference[]>([]);
  const isStreaming = ref(false);

  // SSE 实例
  let sseSource: null | SSE = null;

  /**
   * 发起 SSE 流式请求
   */
  async function startStream(
    options: SseStreamOptions,
  ): Promise<SseStreamResult> {
    const { url, payload } = options;
    const accessStore = useAccessStore();
    const token = accessStore.accessToken;
    const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
    const fullUrl = resolveApiUrl(url, baseUrl);

    // 重置状态
    streamingContent.value = '';
    streamingThinking.value = '';
    streamingReferences.value = [];
    isStreaming.value = true;
    const streamState = createStreamState();

    // 直接使用 SSE 库处理流式响应，错误在 SSE 事件中处理
    return new Promise<SseStreamResult>((resolve, reject) => {
      sseSource = new SSE(fullUrl, {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        payload: JSON.stringify(payload),
        method: 'POST',
      });

      let settled = false;

      function rejectStream(message: string) {
        if (settled) return;
        settled = true;
        const source = sseSource;
        sseSource = null;
        isStreaming.value = false;
        reject(new Error(message));
        source?.close();
      }

      /** 解析并处理错误响应 */
      function handleErrorResponse(responseText: string): boolean {
        if (!responseText || settled) return false;
        try {
          const errorData = JSON.parse(responseText);
          if (errorData.successful === false && errorData.message) {
            rejectStream(errorData.message);
            return true;
          }
          if (errorData.type === 'error' && errorData.message) {
            rejectStream(errorData.message);
            return true;
          }
        } catch {
          // 非 JSON 格式，忽略
        }
        return false;
      }

      /** 完成流式请求 */
      function finishStream() {
        if (settled) return;
        settled = true;
        sseSource = null;
        isStreaming.value = false;
        resolve({
          content: streamingContent.value,
          thinking: streamingThinking.value,
          references: streamingReferences.value,
        });
      }

      sseSource.addEventListener('message', (event: MessageEvent) => {
        const rawData = event.data;
        if (!rawData || settled) return;

        // 先检查是否为错误响应
        if (handleErrorResponse(rawData)) return;

        applySseMessage(rawData, streamState);
        streamingContent.value = streamState.content;
        streamingThinking.value = streamState.thinking;
        streamingReferences.value = [...streamState.references];
      });

      sseSource.addEventListener('error', (e: any) => {
        if (settled) return;
        if (typeof e?.data === 'string' && e.data) {
          try {
            const eventData = JSON.parse(e.data);
            rejectStream(eventData.message || e.data);
          } catch {
            rejectStream(e.data);
          }
          return;
        }
        const xhr = (e as any).source?.xhr || (e as any).target;
        if (xhr?.responseText && handleErrorResponse(xhr.responseText)) return;
        rejectStream('流式请求失败，请重试');
      });

      sseSource.addEventListener('readystatechange', (e: any) => {
        const xhr = e.target;
        if (settled) return;

        if (xhr?.readyState === 4) {
          if (xhr.responseText && handleErrorResponse(xhr.responseText)) return;
          finishStream();
        } else if (e.readyState === 2) {
          finishStream();
        }
      });

      sseSource.addEventListener('abort', () => {
        if (!settled) {
          finishStream();
        }
      });

      sseSource.stream();
    });
  }

  /**
   * 停止流式请求
   */
  function stopStream() {
    isStreaming.value = false;
    if (sseSource) {
      sseSource.close();
      sseSource = null;
    }
  }

  return {
    streamingContent,
    streamingThinking,
    streamingReferences,
    isStreaming,
    startStream,
    stopStream,
  };
}
