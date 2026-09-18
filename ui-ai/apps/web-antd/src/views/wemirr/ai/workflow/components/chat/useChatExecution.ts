/**
 * 对话窗口的一次执行运行态 + SSE 订阅。
 *
 * 每次「发送」产生一个 ChatRun（步骤、流式正文、最终输出），execute-async 拿到
 * executionId 后订阅 GET /workflow-executions/{id}/subscribe 的 SSE 事件流，
 * 按事件类型原地更新这个 ChatRun。
 *
 * 审批：收到 node.paused / workflow.paused 后本轮结束（SSE 关掉、气泡不转圈），
 * 待审批上下文挂在 ChatRun.awaiting 上供面板渲染；结论经 /submit 回传后重新订阅
 * 同一 executionId 的事件流，后续节点补写进同一个气泡（会话历史不断层）。
 *
 * 不复用 components/debug/use-sse.ts：那份实现把事件写进全局 debugStore，
 * 对话是多轮并存的历史，同一条流写全局会把上一轮的状态串掉。
 */

import type {
  NodeCancelledEvent,
  NodeCompletedEvent,
  NodeDeltaEvent,
  NodeFailedEvent,
  NodePausedEvent,
  NodeStartedEvent,
  NodeTimeoutEvent,
  WorkflowCompletedEvent,
  WorkflowFailedEvent,
  WorkflowPausedEvent,
  WorkflowRuntimeEvent,
} from '../../domain/runtime-events';

import type {
  ApprovalContext,
  ApprovalDecisionReq,
} from '#/api/ai-workflow/types';

import { reactive, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { SSE } from 'sse.js';

import {
  cancelExecution,
  executeWorkflowAsync,
  getExecution,
  getExecutionSubscribeUrl,
  submitExecution,
} from '#/api/ai-workflow';

/** 单个节点的执行痕迹 */
export interface ChatStep {
  duration?: number;
  error?: string;
  /** 节点入边输入视图（node.started 带的 input） */
  input?: any;
  /** 画布上的节点名（异步事件不带 label，由 graph 映射补齐） */
  label: string;
  nodeId: string;
  nodeType?: string;
  output?: any;
  status:
    | 'awaiting'
    | 'cancelled'
    | 'completed'
    | 'failed'
    | 'paused'
    | 'running'
    | 'skipped'
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
  status: 'cancelled' | 'error' | 'paused' | 'running' | 'success';
  steps: ChatStep[];
  /** 流式正文增量，按节点聚合 */
  streams: Record<string, string>;
  /** 待审批节点上下文（空=无挂起） */
  awaiting: ApprovalContext[];
  /** 本轮使用的 API Key（再提交/取消要带同一个 key） */
  apiKey?: string;
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
    awaiting: [],
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
    // 输入视图只在节点开始那一刻给，错过这帧执行过程里就没有「输入」可展开
    if (data.input !== undefined && data.input !== null) {
      step.input = data.input;
    }
  }

  function onNodeDelta(run: ChatRun, data: NodeDeltaEvent) {
    if (!data.token) return;
    const bucket = data.reasoning ? run.reasoning : run.streams;
    bucket[data.nodeId] = (bucket[data.nodeId] || '') + data.token;
  }

  function onNodeCompleted(run: ChatRun, data: NodeCompletedEvent) {
    const step = ensureStep(run, data.nodeId);
    // skip：恢复提交里本轮沿用的上轮已完成节点，不能画成正常完成
    step.status = data.skip ? 'skipped' : 'completed';
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

  /** 把审批上下文按节点 id 合并入库（node.paused 先到、workflow.paused 补全） */
  function upsertAwaiting(run: ChatRun, context?: ApprovalContext) {
    if (!context?.nodeId) return;
    const existed = run.awaiting.find((item) => item.nodeId === context.nodeId);
    if (existed) {
      Object.assign(existed, context);
    } else {
      run.awaiting.push(context);
    }
  }

  /** 单个审批节点挂起：本轮还会跑无关分支，先记住待决态 */
  function onNodePaused(run: ChatRun, data: NodePausedEvent) {
    if (!data.nodeId) return;
    ensureStep(run, data.nodeId, data.nodeType as string | undefined).status =
      'awaiting';
    upsertAwaiting(
      run,
      data.approvalContext || {
        nodeId: data.nodeId,
        pauseScope: data.pauseScope,
      },
    );
  }

  /**
   * 整条流本轮跑完但还有未决策的审批节点：停住转圈、关流，等面板提交。
   *
   * 不算终态（不记入 settled=false 的口径），但也要把看门狗拆掉：
   * 后端已经退出 run()，再轮询只会拿到 PAUSED。
   */
  function onWorkflowPaused(run: ChatRun, data: WorkflowPausedEvent) {
    upsertAwaiting(run, data.approvalContext);
    (data.awaitingNodeIds || []).forEach((id) => {
      ensureStep(run, id).status = 'awaiting';
      upsertAwaiting(run, { nodeId: id });
    });
    if (data.nodeId) ensureStep(run, data.nodeId).status = 'awaiting';
    run.duration = data.duration;
    run.status = 'paused';
    clearAwaitingStepsAsPaused(run);
    finish();
  }

  /** 未给上下文的挂起步骤统一标 paused，避免面板进不了审批入口时看不出异常 */
  function clearAwaitingStepsAsPaused(run: ChatRun) {
    const known = new Set(run.awaiting.map((item) => item.nodeId));
    run.steps.forEach((step) => {
      if (step.status === 'awaiting' && !known.has(step.nodeId)) {
        step.status = 'paused';
      }
    });
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
      if (status === 'PAUSED') {
        // 断流后探到暂停：从行内 approvalContext 重建待审批面板数据
        const contexts = detail.approvalContext || {};
        run.awaiting = Object.values(contexts) as ApprovalContext[];
        (
          detail.awaitingNodeIds || run.awaiting.map((item) => item.nodeId)
        ).forEach((id) => {
          ensureStep(run, id).status = 'awaiting';
        });
        run.status = 'paused';
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
      // 只带登录态：订阅是网关与下游共同的白名单路径（executionId 为 UUID 熵足够），
      // 本身不需要 X-Workflow-Token；API Key 模式下网关对 GET .../subscribe 也是直接放行
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
    listen('node.paused', (data) => onNodePaused(run, data as NodePausedEvent));
    listen('workflow.paused', (data) =>
      onWorkflowPaused(run, data as WorkflowPausedEvent),
    );
    // 恢复提交的首帧：上一轮的挂起标记已被本轮重跑，待决列表清空
    listen('workflow.resumed', () => {
      run.awaiting.splice(0);
      run.steps.forEach((step) => {
        if (step.status === 'awaiting' || step.status === 'paused') {
          step.status = 'running';
        }
      });
      run.status = 'running';
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
   * apiKey 是对话选中的 Key，随 execute-async 的 X-Workflow-Token 下发。
   */
  async function send(
    workflowId: number | string,
    inputs: Record<string, any>,
    apiKey?: string,
  ) {
    const executionId = await executeWorkflowAsync(
      workflowId,
      { inputs },
      apiKey,
    );
    const run = createChatRun(executionId);
    run.apiKey = apiKey;
    subscribe(executionId, run);
    return run;
  }

  /**
   * 再提交（需求 3）：同一个 executionId 带上本次结论/输入，提交成功后重新订阅。
   *
   * @param run 本轮对话的运行句柄（携带 executionId / apiKey）
   * @param body 提交体
   * @param body.approval 各待审批节点的结论（传了即按 CONTINUE 恢复）
   * @param body.inputs 可选覆盖的 START 入参
   */
  async function resubmit(
    run: ChatRun,
    body: { approval?: ApprovalDecisionReq[]; inputs?: Record<string, any> },
  ) {
    if (!run?.executionId) return;
    await submitExecution(
      run.executionId,
      { ...body, mode: body.approval ? 'CONTINUE' : 'RETRY' },
      run.apiKey,
    );
    if (body.approval) {
      run.awaiting.splice(0);
    }
    run.error = undefined;
    // 重新订阅同一 executionId：后续节点补写进同一个气泡
    subscribe(run.executionId, run);
  }

  /** 提交审批结论（CONTINUE） */
  async function submitApproval(
    run: ChatRun,
    decisions: ApprovalDecisionReq[],
  ) {
    await resubmit(run, { approval: decisions });
  }

  /** 全量重跑（RETRY）：不传 inputs 则沿用上轮行内输入 */
  async function retryRun(run: ChatRun, inputs?: Record<string, any>) {
    await resubmit(run, inputs ? { inputs } : {});
  }

  /** 主动取消后端执行（引擎在下一个检查点生效；PAUSED 下直接写终态） */
  async function cancelRun(run: ChatRun) {
    if (!run?.executionId) return;
    await cancelExecution(run.executionId, run.apiKey);
  }

  /** 主动停止（关闭连接并标记取消） */
  function abort(run?: ChatRun) {
    if (run && run.status === 'running') run.status = 'cancelled';
    finish();
  }

  return {
    abort,
    cancelRun,
    disconnect: finish,
    retryRun,
    running,
    send,
    submitApproval,
  };
}
