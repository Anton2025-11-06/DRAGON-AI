/**
 * 对话窗口的一次执行运行态 + SSE 订阅。
 *
 * 每次「发送」产生一个 ChatRun（步骤、流式正文、最终输出），execute-async 拿到
 * executionId 后订阅 GET /workflow-executions/{id}/subscribe 的 SSE 事件流，
 * 按事件类型原地更新这个 ChatRun。
 *
 * 不复用 components/debug/use-sse.ts：那份实现把事件写进全局 debugStore，
 * 对话是多轮并存的历史，同一条流写全局会把上一轮的状态串掉。
 */

import type {
  NodeCancelledEvent,
  NodeCompletedEvent,
  NodeDeltaEvent,
  NodeFailedEvent,
  NodeStartedEvent,
  NodeTimeoutEvent,
  WorkflowCompletedEvent,
  WorkflowFailedEvent,
  WorkflowRuntimeEvent,
} from '../../domain/runtime-events';

import { reactive, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { SSE } from 'sse.js';

import {
  executeWorkflowAsync,
  getExecution,
  getExecutionSubscribeUrl,
} from '#/api/ai-workflow';

/** 单个节点的执行痕迹 */
export interface ChatStep {
  duration?: number;
  error?: string;
  /** 画布上的节点名（异步事件不带 label，由 graph 映射补齐） */
  label: string;
  nodeId: string;
  nodeType?: string;
  output?: any;
  status:
    | 'cancelled'
    | 'completed'
    | 'failed'
    | 'paused'
    | 'running'
    | 'timeout';
}

/** 一轮对话（一次工作流执行）的完整状态 */
export interface ChatRun {
  duration?: number;
  error?: string;
  executionId: string;
  /** 终态输出（workflow.completed 的 outputs） */
  outputs: Record<string, any>;
  /** 思维链增量，按节点聚合 */
  reasoning: Record<string, string>;
  status: 'cancelled' | 'error' | 'running' | 'success';
  steps: ChatStep[];
  /** 流式正文增量，按节点聚合 */
  streams: Record<string, string>;
}

export interface ChatExecutionOptions {
  /** 节点ID → 画布节点名 */
  resolveLabel: (nodeId: string, nodeType?: string) => string;
}

/** SSE 单帧 data 的解析（后端帧格式：event: {type}\ndata: {json}） */
function parseFrame<T>(raw: unknown): null | T {
  if (typeof raw !== 'string' || !raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * 一次执行的初始态。
 * 用 reactive 包一层：SSE 回调拿到的是同一个对象，原地写入才能驱动对话气泡更新。
 */
export function createChatRun(executionId: string): ChatRun {
  return reactive({
    executionId,
    outputs: {},
    reasoning: {},
    status: 'running',
    steps: [],
    streams: {},
  }) as ChatRun;
}

export function useChatExecution(options: ChatExecutionOptions) {
  const accessStore = useAccessStore();

  /** 是否有一次执行正在跑（同一时刻只允许一轮，避免两条流写同一屏） */
  const running = ref(false);

  let source: null | SSE = null;
  let activeRun: ChatRun | null = null;
  let pollTimer: null | ReturnType<typeof setTimeout> = null;
  let idleTimer: null | ReturnType<typeof setTimeout> = null;

  /** 事件静默多久后主动查一次执行详情 */
  const IDLE_PROBE_MS = 15_000;

  function clearPoll() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function clearIdle() {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  /** 关闭连接（不改动 run 状态，由调用方决定终态文案） */
  function disconnect() {
    clearPoll();
    clearIdle();
    if (source) {
      source.close();
      source = null;
    }
  }

  /** 事件流是否已经给出终态（决定断开后要不要回落轮询） */
  let settled = false;

  function finish() {
    settled = true;
    running.value = false;
    disconnect();
    activeRun = null;
  }

  function findStep(run: ChatRun, nodeId: string): ChatStep | undefined {
    return run.steps.find((item) => item.nodeId === nodeId);
  }

  function ensureStep(
    run: ChatRun,
    nodeId: string,
    nodeType?: string,
  ): ChatStep {
    const existed = findStep(run, nodeId);
    if (existed) {
      if (nodeType && !existed.nodeType) existed.nodeType = nodeType;
      return existed;
    }
    // 先包成 reactive 再入列：后面拿着引用直接改字段也能驱动视图
    const step = reactive<ChatStep>({
      label: options.resolveLabel(nodeId, nodeType),
      nodeId,
      nodeType,
      status: 'running',
    });
    run.steps.push(step);
    return step;
  }

  // ==================== 事件处理 ====================

  function onNodeStarted(run: ChatRun, data: NodeStartedEvent) {
    const step = ensureStep(run, data.nodeId, data.nodeType);
    step.status = 'running';
    step.label =
      data.nodeName ||
      step.label ||
      options.resolveLabel(data.nodeId, data.nodeType);
  }

  function onNodeDelta(run: ChatRun, data: NodeDeltaEvent) {
    if (!data.token) return;
    const bucket = data.reasoning ? run.reasoning : run.streams;
    bucket[data.nodeId] = (bucket[data.nodeId] || '') + data.token;
  }

  function onNodeCompleted(run: ChatRun, data: NodeCompletedEvent) {
    const step = ensureStep(run, data.nodeId);
    step.status = 'completed';
    step.duration = data.duration;
    step.output = data.output;
  }

  function onNodeFailed(run: ChatRun, data: NodeFailedEvent) {
    const step = ensureStep(run, data.nodeId);
    step.status = 'failed';
    step.error = data.error;
  }

  function onNodeAborted(
    run: ChatRun,
    data: NodeCancelledEvent | NodeTimeoutEvent,
    status: 'cancelled' | 'timeout',
  ) {
    const step = ensureStep(run, data.nodeId);
    // 并行分支的超时/短路不是失败，单独成态，避免对话里冒出一片红
    step.status = status;
    step.duration = data.duration;
    if (data.error) step.error = data.error;
  }

  function onCompleted(run: ChatRun, data: WorkflowCompletedEvent) {
    run.outputs = (data.outputs || {}) as Record<string, any>;
    run.duration = data.duration;
    run.status = 'success';
    finish();
  }

  function onFailed(run: ChatRun, data: WorkflowFailedEvent) {
    run.error = data.error || '执行失败';
    run.status = 'error';
    finish();
  }

  function onCancelled(run: ChatRun) {
    run.status = 'cancelled';
    finish();
  }

  // ==================== 断流回落 ====================

  /**
   * 用执行详情接口探一次终态（服务端事件流的兜底口径）。
   *
   * @returns true 表示已拿到终态并写入 run
   */
  async function probeExecution(run: ChatRun): Promise<boolean> {
    try {
      const detail = await getExecution(run.executionId);
      const status = detail?.status;
      if (status === 'COMPLETED') {
        run.outputs = (detail.outputs || {}) as Record<string, any>;
        run.duration = detail.duration;
        run.status = 'success';
        finish();
        return true;
      }
      if (status === 'FAILED' || status === 'CANCELLED') {
        run.error = detail.errorMessage || '执行未正常结束';
        run.status = status === 'CANCELLED' ? 'cancelled' : 'error';
        finish();
        return true;
      }
    } catch {
      // 查询失败按「未结束」处理，交给下一轮重试，终态判定优先于报错提示
    }
    return false;
  }

  /**
   * 空闲看门狗：每收到一帧就重新计时，静默到点主动查详情。
   *
   * 事件流是 Redis Pub/Sub（无历史），服务端漏发终态事件时流本身不会断，
   * 只看 error/aborted 兜底不够——对话气泡会永远转圈、发送按钮一直 loading。
   */
  function armIdleWatch(run: ChatRun) {
    clearIdle();
    idleTimer = setTimeout(async () => {
      idleTimer = null;
      if (settled) return;
      if (!(await probeExecution(run))) armIdleWatch(run);
    }, IDLE_PROBE_MS);
  }

  /**
   * SSE 没给出终态就断了（网关空闲回收、页面网络抖动）。
   * 执行本身还在后台跑，改用执行详情轮询把结果补齐，避免对话气泡永远转圈。
   */
  function reconcile(run: ChatRun, attempt = 0) {
    if (settled) return;
    const maxAttempt = 5;
    clearPoll();
    pollTimer = setTimeout(async () => {
      pollTimer = null;
      if (settled) return;
      if (await probeExecution(run)) return;
      if (attempt < maxAttempt) {
        reconcile(run, attempt + 1);
      } else {
        run.error = run.error || '事件连接中断，未能获取执行结果';
        run.status = 'error';
        finish();
      }
    }, 2000);
  }

  // ==================== 订阅 ====================

  function subscribe(executionId: string, run: ChatRun) {
    disconnect();
    settled = false;
    activeRun = run;
    running.value = true;

    const url = getExecutionSubscribeUrl(executionId);
    source = new SSE(url, {
      headers: { Authorization: `Bearer ${accessStore.accessToken || ''}` },
      method: 'GET',
      start: false,
    });

    const listen = (type: string, handler: (data: any) => void) => {
      source?.addEventListener(type, (event: MessageEvent) => {
        const data = parseFrame<WorkflowRuntimeEvent>(event.data);
        if (!data || !activeRun) return;
        // 收到任何一帧都说明流是活的，看门狗重新计时
        armIdleWatch(run);
        handler(data);
      });
    };

    listen('node.started', (data) =>
      onNodeStarted(run, data as NodeStartedEvent),
    );
    listen('node.delta', (data) => onNodeDelta(run, data as NodeDeltaEvent));
    listen('node.completed', (data) =>
      onNodeCompleted(run, data as NodeCompletedEvent),
    );
    listen('node.failed', (data) => onNodeFailed(run, data as NodeFailedEvent));
    listen('node.timeout', (data) =>
      onNodeAborted(run, data as NodeTimeoutEvent, 'timeout'),
    );
    listen('node.cancelled', (data) =>
      onNodeAborted(run, data as NodeCancelledEvent, 'cancelled'),
    );
    listen('workflow.completed', (data) =>
      onCompleted(run, data as WorkflowCompletedEvent),
    );
    listen('workflow.failed', (data) =>
      onFailed(run, data as WorkflowFailedEvent),
    );
    listen('workflow.cancelled', () => onCancelled(run));
    listen('workflow.paused', (data) => {
      const paused = data as { nodeId?: string };
      if (paused?.nodeId) ensureStep(run, paused.nodeId).status = 'paused';
    });

    // 服务端正常收尾也会走这里：没有终态就轮询补
    source.addEventListener('error', () => {
      if (!settled) reconcile(run);
    });
    source.addEventListener('aborted', () => {
      if (!settled) reconcile(run);
    });

    source.stream();
    // 订阅即起表：极短的执行可能一个事件都没推给本订阅者
    armIdleWatch(run);
  }

  /**
   * 发起一轮对话：异步执行 + 订阅事件流，返回被填充的 ChatRun。
   */
  async function send(
    workflowId: number | string,
    inputs: Record<string, any>,
  ) {
    const executionId = await executeWorkflowAsync(workflowId, { inputs });
    const run = createChatRun(executionId);
    subscribe(executionId, run);
    return run;
  }

  /** 主动停止（关闭连接并标记取消） */
  function abort(run?: ChatRun) {
    if (run && run.status === 'running') run.status = 'cancelled';
    finish();
  }

  return {
    abort,
    disconnect: finish,
    running,
    send,
  };
}
