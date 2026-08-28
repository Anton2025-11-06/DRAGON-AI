/**
 * SSE 事件处理 Composable
 * 处理工作流执行的 Server-Sent Events
 * 支持自动重连、手动重试、错误状态管理
 */

import type { WorkflowRuntimeEventType } from '../../domain/runtime-events';

import type { NodeType } from '#/api/ai-workflow/types';

import {
  computed,
  getCurrentInstance,
  onBeforeUnmount,
  ref,
  shallowRef,
} from 'vue';

import { useAccessStore } from '@vben/stores';

import { message } from 'ant-design-vue';
import { SSE } from 'sse.js';

import { getExecutionSubscribeUrl } from '#/api/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

// ==================== 类型定义 ====================

/**
 * SSE 事件类型
 */
export type SSEEventType = WorkflowRuntimeEventType;

/**
 * SSE 连接状态
 */
export type SSEConnectionState =
  | 'connected'
  | 'connecting'
  | 'disconnected'
  | 'error'
  | 'reconnecting';

/**
 * 网络错误类型
 */
export type NetworkErrorType =
  | 'CONNECTION_FAILED'
  | 'CONNECTION_LOST'
  | 'SERVER_ERROR'
  | 'TIMEOUT'
  | 'UNKNOWN';

/**
 * 网络错误信息
 */
export interface NetworkError {
  type: NetworkErrorType;
  message: string;
  timestamp: Date;
  retryable: boolean;
  retryCount: number;
}

/**
 * 节点开始事件数据
 */
export interface NodeStartedEventData {
  nodeId: string;
  nodeName: string;
  nodeType: NodeType;
  input?: any;
}

/**
 * 节点完成事件数据
 */
export interface NodeCompletedEventData {
  nodeId: string;
  output?: any;
  duration?: number;
  tokenUsage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
  httpDetails?: {
    method: string;
    responseTime: number;
    statusCode: number;
    url: string;
  };
}

/**
 * 节点错误事件数据
 */
export interface NodeErrorEventData {
  nodeId: string;
  error: string;
  stackTrace?: string;
}

/**
 * 流式 Token 事件数据
 */
export interface StreamTokenEventData {
  nodeId: string;
  token: string;
}

/**
 * 断点命中事件数据
 */
export interface BreakpointHitEventData {
  nodeId: string;
  variables?: Record<string, any>;
}

/**
 * 执行完成事件数据
 */
export interface ExecutionCompletedEventData {
  outputs?: Record<string, any>;
  duration?: number;
}

/**
 * 执行失败事件数据
 */
export interface ExecutionFailedEventData {
  error: string;
}

/**
 * 执行取消事件数据
 */
export interface ExecutionCancelledEventData {
  reason?: string;
}

/**
 * SSE 事件回调
 */
export interface SSEEventCallbacks {
  onNodeStarted?: (data: NodeStartedEventData) => void;
  onNodeCompleted?: (data: NodeCompletedEventData) => void;
  onNodeError?: (data: NodeErrorEventData) => void;
  onStreamToken?: (data: StreamTokenEventData) => void;
  onBreakpointHit?: (data: BreakpointHitEventData) => void;
  onExecutionCompleted?: (data: ExecutionCompletedEventData) => void;
  onExecutionFailed?: (data: ExecutionFailedEventData) => void;
  onExecutionCancelled?: (data: ExecutionCancelledEventData) => void;
  onConnectionStateChange?: (state: SSEConnectionState) => void;
  /** 网络错误回调*/
  onNetworkError?: (error: NetworkError) => void;
}

/**
 * SSE 配置选项
 */
export interface SSEOptions {
  /** 最大重连次数 */
  maxReconnectAttempts?: number;
  /** 初始重连延迟(毫秒) */
  initialReconnectDelay?: number;
  /** 最大重连延迟(毫秒) */
  maxReconnectDelay?: number;
  /** 是否启用自动重连 */
  autoReconnect?: boolean;
  /** 连接超时时间(毫秒) */
  connectionTimeout?: number;
}

// ==================== 默认配置 ====================

const DEFAULT_OPTIONS: Required<SSEOptions> = {
  maxReconnectAttempts: 5,
  initialReconnectDelay: 1000,
  maxReconnectDelay: 30_000,
  autoReconnect: true,
  connectionTimeout: 30_000,
};

// ==================== Composable ====================

/**
 * SSE 事件处理 Composable
 * @param baseUrl SSE 基础 URL
 * @param callbacks 事件回调
 * @param options 配置选项
 */
export function useSSE(
  baseUrl: string = '/api',
  callbacks?: SSEEventCallbacks,
  options?: SSEOptions,
) {
  const debugStore = useDebugStore();
  const accessStore = useAccessStore();
  const config = { ...DEFAULT_OPTIONS, ...options };

  // ==================== State ====================

  /** SSE 实例 */
  const eventSource = shallowRef<null | SSE>(null);

  /** 连接状态 */
  const connectionState = ref<SSEConnectionState>('disconnected');

  /** 重连次数 */
  const reconnectAttempts = ref(0);

  /** 当前执行ID */
  const currentExecutionId = ref<null | string>(null);

  /** 网络错误信息*/
  const networkError = ref<NetworkError | null>(null);

  /** 是否正在重连 */
  const isReconnecting = ref(false);

  /** 重连定时器 */
  let reconnectTimer: null | ReturnType<typeof setTimeout> = null;

  /** 连接超时定时器 */
  let connectionTimeoutTimer: null | ReturnType<typeof setTimeout> = null;

  // ==================== Computed ====================

  /** 是否可以重试*/
  const canRetry = computed(() => {
    return (
      connectionState.value === 'error' &&
      networkError.value?.retryable &&
      currentExecutionId.value !== null
    );
  });

  /** 是否已达到最大重连次数 */
  const hasReachedMaxRetries = computed(() => {
    return reconnectAttempts.value >= config.maxReconnectAttempts;
  });

  /** 下次重连延迟(毫秒) */
  const nextReconnectDelay = computed(() => {
    // 使用指数退避策略
    const delay = config.initialReconnectDelay * 2 ** reconnectAttempts.value;
    return Math.min(delay, config.maxReconnectDelay);
  });

  // ==================== Methods ====================

  /**
   * 连接 SSE
   * @param executionId 执行ID
   */
  function connect(executionId: string) {
    // 关闭现有连接
    disconnect(false);

    currentExecutionId.value = executionId;
    connectionState.value = 'connecting';
    networkError.value = null;
    callbacks?.onConnectionStateChange?.('connecting');

    const url = getExecutionSubscribeUrl(executionId, baseUrl);

    try {
      eventSource.value = new SSE(url, {
        headers: buildHeaders(),
        method: 'GET',
        start: false,
      });

      // 设置连接超时
      startConnectionTimeout(executionId);

      // 连接成功
      eventSource.value.addEventListener('open', () => {
        clearConnectionTimeout();
        connectionState.value = 'connected';
        reconnectAttempts.value = 0;
        networkError.value = null;
        callbacks?.onConnectionStateChange?.('connected');

        if (isReconnecting.value) {
          message.success('连接已恢复');
        }
        isReconnecting.value = false;
      });

      // 连接错误
      eventSource.value.addEventListener('error', () => {
        clearConnectionTimeout();
        handleConnectionError('CONNECTION_LOST', '连接断开');
      });

      // 注册事件监听器
      registerEventListeners();
      eventSource.value.stream();
    } catch {
      clearConnectionTimeout();
      handleConnectionError('CONNECTION_FAILED', '无法建立连接');
    }
  }

  /**
   * 处理连接错误
   */
  function handleConnectionError(type: NetworkErrorType, errorMessage: string) {
    connectionState.value = 'error';

    const error: NetworkError = {
      type,
      message: errorMessage,
      timestamp: new Date(),
      retryable: type !== 'SERVER_ERROR',
      retryCount: reconnectAttempts.value,
    };

    networkError.value = error;
    callbacks?.onConnectionStateChange?.('error');
    callbacks?.onNetworkError?.(error);

    // 自动重连
    if (
      config.autoReconnect &&
      currentExecutionId.value &&
      !hasReachedMaxRetries.value
    ) {
      scheduleReconnect(currentExecutionId.value);
    } else if (hasReachedMaxRetries.value) {
      message.error('连接失败，已达到最大重试次数。请点击重试按钮手动重连。');
    }
  }

  /**
   * 设置连接超时
   */
  function startConnectionTimeout(_executionId: string) {
    clearConnectionTimeout();
    connectionTimeoutTimer = setTimeout(() => {
      if (connectionState.value === 'connecting') {
        handleConnectionError('TIMEOUT', '连接超时');
      }
    }, config.connectionTimeout);
  }

  /**
   * 清除连接超时
   */
  function clearConnectionTimeout() {
    if (connectionTimeoutTimer) {
      clearTimeout(connectionTimeoutTimer);
      connectionTimeoutTimer = null;
    }
  }

  /**
   * 注册 SSE 事件监听器
   */
  function registerEventListeners() {
    if (!eventSource.value) return;
    const addEventListener = (
      type: string,
      listener: (event: MessageEvent) => void,
    ) => {
      eventSource.value?.addEventListener(type, listener);
    };

    // 节点开始事件
    addEventListener('node.started', (e) => {
      const data = parseEventData<NodeStartedEventData>(e);
      if (data) {
        debugStore.handleNodeStart({
          type: 'node.started',
          nodeId: data.nodeId,
          nodeName: data.nodeName,
          nodeType: data.nodeType,
          input: data.input,
        });
        callbacks?.onNodeStarted?.(data);
      }
    });

    // 节点完成事件
    addEventListener('node.completed', (e) => {
      const data = parseEventData<NodeCompletedEventData>(e);
      if (data) {
        debugStore.handleNodeComplete({
          type: 'node.completed',
          nodeId: data.nodeId,
          output: data.output,
          duration: data.duration,
          tokenUsage: data.tokenUsage,
          httpDetails: data.httpDetails,
        });
        callbacks?.onNodeCompleted?.(data);
      }
    });

    // 节点失败事件
    addEventListener('node.failed', (e) => {
      const data = parseEventData<NodeErrorEventData>(e);
      if (data) {
        debugStore.handleNodeError({
          type: 'node.failed',
          nodeId: data.nodeId,
          error: data.error,
          stackTrace: data.stackTrace,
        });
        callbacks?.onNodeError?.(data);
      }
    });

    // 节点增量输出事件
    addEventListener('node.delta', (e) => {
      const data = parseEventData<StreamTokenEventData>(e);
      if (data) {
        debugStore.handleStreamingToken({
          type: 'node.delta',
          nodeId: data.nodeId,
          token: data.token,
        });
        callbacks?.onStreamToken?.(data);
      }
    });

    // 工作流暂停事件
    addEventListener('workflow.paused', (e) => {
      const data = parseEventData<BreakpointHitEventData>(e);
      if (data) {
        debugStore.handleBreakpointHit({
          type: 'workflow.paused',
          nodeId: data.nodeId,
          variables: data.variables,
        });
        callbacks?.onBreakpointHit?.(data);
      }
    });

    // 工作流完成事件
    addEventListener('workflow.completed', (e) => {
      const data = parseEventData<ExecutionCompletedEventData>(e);
      if (data) {
        debugStore.completeExecution(data.outputs || {}, data.duration || 0);
        callbacks?.onExecutionCompleted?.(data);
        disconnect();
      }
    });

    // 工作流失败事件
    addEventListener('workflow.failed', (e) => {
      const data = parseEventData<ExecutionFailedEventData>(e);
      if (data) {
        debugStore.failExecution(data.error);
        callbacks?.onExecutionFailed?.(data);
        disconnect();
      }
    });

    // 工作流取消事件
    addEventListener('workflow.cancelled', (e) => {
      const data = parseEventData<ExecutionCancelledEventData>(e);
      if (data) {
        debugStore.cancelExecution();
        callbacks?.onExecutionCancelled?.(data);
        disconnect();
      }
    });
  }

  /**
   * 解析事件数据
   */
  function parseEventData<T>(event: Event): null | T {
    try {
      const messageEvent = event as MessageEvent;
      return JSON.parse(messageEvent.data) as T;
    } catch {
      return null;
    }
  }

  /**
   * 安排重连
   */
  function scheduleReconnect(executionId: string) {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }

    reconnectAttempts.value++;
    isReconnecting.value = true;
    connectionState.value = 'reconnecting';
    callbacks?.onConnectionStateChange?.('reconnecting');

    const delay = nextReconnectDelay.value;

    message.warning(
      `连接断开，${(delay / 1000).toFixed(1)}秒后重试 (${reconnectAttempts.value}/${config.maxReconnectAttempts})`,
    );

    reconnectTimer = setTimeout(() => {
      connect(executionId);
    }, delay);
  }

  /**
   * 手动重试连接
   */
  function retry() {
    if (!currentExecutionId.value) {
      message.error('没有可重试的连接');
      return;
    }

    // 重置重连计数
    reconnectAttempts.value = 0;
    networkError.value = null;

    message.info('正在重新连接...');
    connect(currentExecutionId.value);
  }

  /**
   * 取消重连
   */
  function cancelReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    isReconnecting.value = false;
    connectionState.value = 'error';
    callbacks?.onConnectionStateChange?.('error');
    message.info('已取消自动重连');
  }

  /**
   * 断开连接
   * @param clearState 是否清除状态
   */
  function disconnect(clearState: boolean = true) {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    clearConnectionTimeout();

    if (eventSource.value) {
      eventSource.value.close();
      eventSource.value = null;
    }

    if (clearState) {
      currentExecutionId.value = null;
      reconnectAttempts.value = 0;
      networkError.value = null;
      isReconnecting.value = false;
    }

    connectionState.value = 'disconnected';
    callbacks?.onConnectionStateChange?.('disconnected');
  }

  /**
   * 检查是否已连接
   */
  function isConnected(): boolean {
    return connectionState.value === 'connected';
  }

  /**
   * 获取连接状态描述
   */
  function getConnectionStateDescription(): string {
    const descriptions: Record<SSEConnectionState, string> = {
      disconnected: '未连接',
      connecting: '正在连接...',
      connected: '已连接',
      error: '连接错误',
      reconnecting: `正在重连 (${reconnectAttempts.value}/${config.maxReconnectAttempts})...`,
    };
    return descriptions[connectionState.value];
  }

  // ==================== Lifecycle ====================

  if (getCurrentInstance()) {
    onBeforeUnmount(() => {
      disconnect();
    });
  }

  function buildHeaders(): Record<string, string> {
    if (!accessStore.accessToken) {
      return {};
    }
    return {
      Authorization: `Bearer ${accessStore.accessToken}`,
    };
  }

  // ==================== Return ====================

  return {
    /** 连接状态 */
    connectionState,
    /** 重连次数 */
    reconnectAttempts,
    /** 网络错误信息 */
    networkError,
    /** 是否正在重连 */
    isReconnecting,
    /** 是否可以重试 */
    canRetry,
    /** 是否已达到最大重连次数 */
    hasReachedMaxRetries,
    /** 下次重连延迟 */
    nextReconnectDelay,
    /** 连接 SSE */
    connect,
    /** 断开连接 */
    disconnect,
    /** 手动重试 */
    retry,
    /** 取消重连 */
    cancelReconnect,
    /** 检查是否已连接 */
    isConnected,
    /** 获取连接状态描述 */
    getConnectionStateDescription,
  };
}
