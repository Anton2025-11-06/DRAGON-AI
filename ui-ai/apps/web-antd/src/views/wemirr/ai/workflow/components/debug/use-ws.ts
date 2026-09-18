/**
 * 工作流「预览运行 / 预览再提交」WebSocket 事件接收 Composable
 *
 * 取代原 execute-async + SSE(订阅 Redis) 链路：连接建立后即向 execute-sync 端点
 * 发送 WorkflowExecutionReq 入参触发执行（或向 submit-sync 发 WorkflowSubmitReq 带
 * 审批结论继续跑），执行事件（node.started / node.delta / workflow.completed 等）
 * 由服务端经同一 socket 实时回推。
 *
 * 事件对象结构与 SSE data 完全一致（{type, executionId, timestamp, ...payload}），
 * 因此复用 debugStore.handleSSEEvent 做统一分发，回调签名沿用 use-sse。
 */
import type { SSEConnectionState, SSEEventCallbacks } from './use-sse';

import type {
  WorkflowExecutionReq,
  WorkflowSubmitReq,
} from '#/api/ai-workflow/types';

import { getCurrentInstance, onBeforeUnmount, ref, shallowRef } from 'vue';

import { useAccessStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import {
  getWorkflowExecuteSyncWsUrl,
  getWorkflowSubmitSyncWsUrl,
} from '#/api/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

/** 终态事件：收到后主动关闭连接 */
const TERMINAL_TYPES = new Set([
  'workflow.cancelled',
  'workflow.completed',
  'workflow.failed',
]);

export function useWorkflowWs(
  baseUrl: string = '/api',
  callbacks?: SSEEventCallbacks,
) {
  const debugStore = useDebugStore();
  const accessStore = useAccessStore();

  /** WebSocket 实例 */
  const socket = shallowRef<null | WebSocket>(null);

  /** 连接状态（与 use-sse 共用同一状态枚举，便于 UI 复用） */
  const connectionState = ref<SSEConnectionState>('disconnected');

  /** 待连接建立后发送的执行入参 */
  let pendingBody = '';

  /** 心跳定时器：应用层 ping，防反向代理空闲回收（浏览器无法发协议层 ping） */
  let heartbeatTimer: null | ReturnType<typeof setInterval> = null;

  function setState(state: SSEConnectionState) {
    connectionState.value = state;
    callbacks?.onConnectionStateChange?.(state);
  }

  /** 启动应用层心跳：每 3s 发一帧 {type:'ping'}，由网关就地回 pong */
  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(() => {
      const ws = socket.value;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 3000);
  }

  /** 停止心跳 */
  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  /**
   * 建立连接并触发执行
   * @param workflowId 工作流ID
   * @param body 执行入参（inputs）
   */
  function connect(workflowId: number | string, body: WorkflowExecutionReq) {
    open(getWorkflowExecuteSyncWsUrl(workflowId, baseUrl), {
      Authorization: buildAuth(),
      ...body,
    });
  }

  /**
   * 建立连接并再提交（预览页审批入口）
   * @param executionId 执行ID（与首跑同一 id，会话语义连续）
   * @param body 提交入参（mode / inputs / approval）
   */
  function connectSubmit(executionId: string, body: WorkflowSubmitReq) {
    open(getWorkflowSubmitSyncWsUrl(executionId, baseUrl), {
      Authorization: buildAuth(),
      ...body,
    });
  }

  /** 浏览器 WebSocket 无法自定义握手头，token 随首帧 JSON 透传给网关 */
  function buildAuth(): string {
    return accessStore.accessToken ? `Bearer ${accessStore.accessToken}` : '';
  }

  /** 拨号并发送首帧（execute-sync / submit-sync 共用） */
  function open(url: string, body: Record<string, any>) {
    disconnect();
    setState('connecting');
    // 网关读取 Authorization 解析登录态后注入 login_user 再转发下游
    pendingBody = JSON.stringify(body);

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      setState('error');
      message.error('无法建立执行连接');
      return;
    }
    socket.value = ws;

    ws.addEventListener('open', () => {
      setState('connected');
      ws.send(pendingBody);
      startHeartbeat();
    });
    ws.addEventListener('message', (event: MessageEvent) =>
      handleMessage(event.data),
    );
    ws.addEventListener('error', () => setState('error'));
    ws.addEventListener('close', () => {
      socket.value = null;
      if (connectionState.value !== 'error') setState('disconnected');
    });
  }

  /** 解析并分发单条 WS 消息 */
  function handleMessage(raw: unknown) {
    if (typeof raw !== 'string') return;
    let data: any;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }
    if (!data || typeof data !== 'object') return;

    // 心跳 pong：网关就地回帧，与业务事件无关，直接忽略（不喂给 debugStore）
    if (data.type === 'ping' || data.type === 'pong') return;

    // 后端 execute-sync 收尾帧：{code, message}（无 type 字段）。
    // 2xx 视为正常结束（如暂停后 run() 返回触发的收尾），仅断开不覆盖已有结果；
    // 其余（如异常兜底 500）按执行失败处理。
    if (!data.type && (data.code !== undefined || data.message)) {
      const code = Number(data.code ?? 200);
      if (code >= 200 && code < 300) {
        disconnect();
      } else {
        const err = data.message || '执行失败';
        debugStore.failExecution(err);
        callbacks?.onExecutionFailed?.({ error: err });
        disconnect();
      }
      return;
    }

    // 首个事件（workflow.started）携带 executionId，回填供停止/继续等 HTTP 接口使用
    if (data.executionId && debugStore.executionId !== data.executionId) {
      debugStore.executionId = data.executionId;
    }

    debugStore.handleSSEEvent(data);
    fireCallbacks(data);

    if (TERMINAL_TYPES.has(data.type)) disconnect();
  }

  /** 触发外部回调（与 use-sse 的回调语义一致） */
  function fireCallbacks(data: any) {
    switch (data.type) {
      case 'node.cancelled': {
        callbacks?.onNodeCancelled?.(data);
        break;
      }
      case 'node.completed': {
        callbacks?.onNodeCompleted?.(data);
        break;
      }
      case 'node.delta': {
        callbacks?.onStreamToken?.(data);
        break;
      }
      case 'node.failed': {
        callbacks?.onNodeError?.(data);
        break;
      }
      case 'node.paused': {
        callbacks?.onNodePaused?.(data);
        break;
      }
      case 'node.started': {
        callbacks?.onNodeStarted?.(data);
        break;
      }
      case 'node.timeout': {
        callbacks?.onNodeTimeout?.(data);
        break;
      }
      case 'workflow.cancelled': {
        callbacks?.onExecutionCancelled?.(data);
        break;
      }
      case 'workflow.completed': {
        callbacks?.onExecutionCompleted?.(data);
        break;
      }
      case 'workflow.failed': {
        callbacks?.onExecutionFailed?.(data);
        break;
      }
      case 'workflow.paused': {
        callbacks?.onApprovalPaused?.(data);
        break;
      }
    }
  }

  /** 关闭连接 */
  function disconnect() {
    stopHeartbeat();
    if (socket.value) {
      try {
        socket.value.close();
      } catch {
        // 忽略关闭异常
      }
      socket.value = null;
    }
    setState('disconnected');
  }

  if (getCurrentInstance()) {
    onBeforeUnmount(() => disconnect());
  }

  return {
    connectionState,
    connect,
    connectSubmit,
    disconnect,
  };
}
