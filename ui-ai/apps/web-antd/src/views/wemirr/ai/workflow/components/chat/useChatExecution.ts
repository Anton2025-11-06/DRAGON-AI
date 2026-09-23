/**
 * 对话窗口的一次执行运行态 + SSE 订阅：一个会话固定一个 executionId，每轮在同一 id 上提交。
 * 待审批只从执行详情的 pendingApprovals 读（帧上不承载审批内容），谁要画面板谁刷新。
 * 不复用 components/debug/use-sse.ts：那份把事件写进全局 debugStore，撑不住多轮并存的历史。
 */

import type {
  NodeCancelledEvent,
  NodeCompletedEvent,
  NodeDeltaEvent,
  NodeFailedEvent,
  NodePausedEvent,
  NodeStartedEvent,
  NodeTimeoutEvent,
  NodeToolCallEvent,
  NodeToolResultEvent,
  WorkflowCompletedEvent,
  WorkflowFailedEvent,
  WorkflowPausedEvent,
  WorkflowRuntimeEvent,
} from '../../domain/runtime-events';

import type {
  ApprovalDecisionReq,
  LlmToolKind,
  PendingApproval,
} from '#/api/ai-workflow/types';

import { reactive, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { SSE } from 'sse.js';

import {
  cancelExecution,
  getExecution,
  getExecutionSubscribeUrl,
  submitWorkflow,
} from '#/api/ai-workflow';

/** 一次工具调用的痕迹（node.tool_call 开条、node.tool_result 回填结果） */
export interface ChatToolCall {
  arguments?: Record<string, any>;
  error?: null | string;
  kind?: LlmToolKind;
  round?: number;
  result?: string;
  resumed?: boolean;
  toolCallId?: string;
  toolName?: string;
}

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
  /** 插入工具后的调用过程（按事件到达顺序追加） */
  toolCalls?: ChatToolCall[];
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
  /** 此刻欠人答的审批（执行详情 pendingApprovals 的镜像，空=无挂起） */
  pendingApprovals: PendingApproval[];
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
    executionId,
    outputs: {},
    pendingApprovals: [],
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

  /**
   * 断流后最多重新订阅几次（父等子的那段静默窗口可被网关回收连接，需要自愈）。
   * 不封顶就会在「服务端一直不返终态帧」的异常场景下订阅风暴，给一个足够宽的数。
   */
  const MAX_RESUBSCRIBE = 20;
  let resubscribes = 0;

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

  /**
   * 工具调用 / 结果：归到发起调用的那个节点步骤上。
   *
   * 节点可能因取消/断流错过 node.started，ensureStep 兼容：至少调用过程要可见。
   * 结果帧靠 toolCallId 配对（模型一轮可并行发多个调用）。
   */
  function onToolCall(run: ChatRun, data: NodeToolCallEvent) {
    if (!data.nodeId) return;
    const step = ensureStep(run, data.nodeId);
    step.toolCalls = [
      ...(step.toolCalls || []),
      {
        arguments: data.arguments,
        kind: data.toolKind,
        round: data.round,
        resumed: data.resumed,
        toolCallId: data.toolCallId,
        toolName: data.toolName,
      },
    ];
  }

  function onToolResult(run: ChatRun, data: NodeToolResultEvent) {
    if (!data.nodeId) return;
    const step = ensureStep(run, data.nodeId);
    const existed = (step.toolCalls || []).find(
      (item) => item.toolCallId && item.toolCallId === data.toolCallId,
    );
    if (existed) {
      existed.error = data.error ?? null;
      existed.result = data.result;
      if (data.resumed !== undefined) existed.resumed = data.resumed;
      return;
    }
    step.toolCalls = [
      ...(step.toolCalls || []),
      {
        error: data.error ?? null,
        kind: data.toolKind,
        result: data.result,
        round: data.round,
        resumed: data.resumed,
        toolCallId: data.toolCallId,
        toolName: data.toolName,
      },
    ];
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

  /**
   * 用执行详情刷新本轮欠人答的审批（审批内容的唯一来源）。
   * 详情读不到时保持原样：读失败不能当成「没有欠着的审批」。
   */
  async function refreshPendingApprovals(run: ChatRun) {
    try {
      const detail = await getExecution(run.executionId);
      if (detail) run.pendingApprovals = detail.pendingApprovals || [];
    } catch {
      // 查不动详情：面板维持现状，由下一轮探测或用户重试补上
    }
  }

  /** 单个审批节点挂起：本轮还会跑无关分支，先把这个节点画成等人答 */
  function onNodePaused(run: ChatRun, data: NodePausedEvent) {
    if (!data.nodeId) return;
    ensureStep(run, data.nodeId, data.nodeType as string | undefined).status =
      'awaiting';
  }

  /**
   * 整条流本轮跑完但停在等人：停住转圈、关流，再从详情取待办给面板。
   *
   * 不算终态，但也要把看门狗拆掉：后端已经退出 run()，再轮询只会拿到 PAUSED。
   */
  async function onWorkflowPaused(run: ChatRun, data: WorkflowPausedEvent) {
    if (data.nodeId) ensureStep(run, data.nodeId).status = 'awaiting';
    run.duration = data.duration;
    run.status = 'paused';
    finish();
    await refreshPendingApprovals(run);
    clearAwaitingStepsAsPaused(run);
  }

  /** 标了等人答但详情里没这份待办的步骤降级为 paused：看不出新问题比把不存在的审批摆上面板好 */
  function clearAwaitingStepsAsPaused(run: ChatRun) {
    const known = new Set(run.pendingApprovals.map((item) => item.nodeId));
    run.steps.forEach((step) => {
      if (step.status === 'awaiting' && !known.has(step.nodeId)) {
        step.status = 'paused';
      }
    });
  }

  // ==================== 断流回落 ====================

  /** 探一次详情的结论：拿到终态 / 确知还在跑（含「等子流程跑完」）/ 探不出东西 */
  type ProbeResult = 'alive' | 'settled' | 'unknown';

  /**
   * 用执行详情接口探一次终态（服务端事件流的兜底口径）。
   *
   * 返回 'alive' 与 'unknown' 的差别决定断流后是「重新订阅接着等」还是
   * 「按尝试次数报错」：父等子的那段窗口里服务端故意不发任何挂起帧（否则页面
   * 会把刚提交过的审批再弹一遍），连接一旦被网关回收，按'unknown' 处理就会
   * 在十秒后把一条正常在跑的执行报成「事件连接中断」。
   */
  async function probeExecution(run: ChatRun): Promise<ProbeResult> {
    try {
      const detail = await getExecution(run.executionId);
      const status = detail?.status;
      if (status === 'COMPLETED') {
        run.outputs = (detail.outputs || {}) as Record<string, any>;
        run.duration = detail.duration;
        run.status = 'success';
        finish();
        return 'settled';
      }
      if (status === 'FAILED' || status === 'CANCELLED') {
        run.error = detail.errorMessage || '执行未正常结束';
        run.status = status === 'CANCELLED' ? 'cancelled' : 'error';
        finish();
        return 'settled';
      }
      if (status === 'PAUSED') {
        run.pendingApprovals = detail.pendingApprovals || [];
        // 清单为空 = 本轮不欠人答，只是停在等子执行跑完被钩子推起：接着探。
        // 把这种 PAUSED 当待决项渲染，就是把已经投递的结论再问一遍
        if (run.pendingApprovals.length === 0) return 'alive';
        run.pendingApprovals.forEach((item) => {
          ensureStep(run, item.nodeId).status = 'awaiting';
        });
        run.status = 'paused';
        finish();
        return 'settled';
      }
      // 还停在 RUNNING/PENDING：执行在 worker 手里，接着等就是对的
      return 'alive';
    } catch {
      // 查不动详情：不能据此断定执行还活着，也不能就此报失败，交给下一轮重试
      return 'unknown';
    }
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
      if ((await probeExecution(run)) !== 'settled') armIdleWatch(run);
    }, IDLE_PROBE_MS);
  }

  /**
   * SSE 没给出终态就断了（网关空闲回收、页面网络抖动）。
   * 执行本身还在后台跑，改用执行详情轮询把结果补齐，避免对话气泡永远转圈。
   *
   * 探明「确实还活着」时不是接着数尝试次数，而是重新订阅一次：服务端会按行状态
   * 分流（RUNNING 挂频道；已转发结论的 PAUSED 补历史后挂频道等续跑），比在这干轮询
   * 靠谱。重试次数只用于兜住「订阅→立即断流」的循环。
   */
  function reconcile(run: ChatRun, attempt = 0) {
    if (settled) return;
    const maxAttempt = 5;
    clearPoll();
    pollTimer = setTimeout(async () => {
      pollTimer = null;
      if (settled) return;
      const probe = await probeExecution(run);
      if (probe === 'settled') return;
      if (probe === 'alive' && resubscribes < MAX_RESUBSCRIBE) {
        resubscribes += 1;
        subscribe(run.executionId, run);
        return;
      }
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
        // 收到任何一帧都说明流是活的：看门狗重新计时，断流重订阅的额度也重新给满
        armIdleWatch(run);
        resubscribes = 0;
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
    listen('node.tool_call', (data) =>
      onToolCall(run, data as NodeToolCallEvent),
    );
    listen('node.tool_result', (data) =>
      onToolResult(run, data as NodeToolResultEvent),
    );
    listen('workflow.completed', (data) =>
      onCompleted(run, data as WorkflowCompletedEvent),
    );
    listen('workflow.failed', (data) =>
      onFailed(run, data as WorkflowFailedEvent),
    );
    listen('workflow.cancelled', () => onCancelled(run));
    listen('node.paused', (data) => onNodePaused(run, data as NodePausedEvent));
    listen('workflow.paused', (data) => {
      void onWorkflowPaused(run, data as WorkflowPausedEvent);
    });
    // 恢复提交的首帧：上一轮的挂起标记已被本轮重跑，待办清单随之作废
    listen('workflow.resumed', () => {
      run.pendingApprovals = [];
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
   * 发起一轮对话：新建一条执行拿到 executionId，订阅事件流并返回被填充的 ChatRun。
   * apiKey 是对话选中的 Key，随 submit 的 X-Workflow-Token 下发。
   */
  async function send(
    workflowId: number | string,
    values: Record<string, any>,
    apiKey?: string,
  ) {
    const result = await submitWorkflow({ values, workflowId }, apiKey);
    const run = createChatRun(result.executionId);
    run.apiKey = apiKey;
    subscribe(result.executionId, run);
    return run;
  }

  /**
   * 审批结论回传：同一个 executionId 上接着跑，后续节点补写进同一个气泡。
   *
   * 只有审批保持原地：它就是本轮的下半段，另起气泡会把一次回答剖成两截。
   *
   * @param run 本轮对话的运行句柄（携带 executionId / apiKey）
   * @param decisions 各待审批节点的结论（approvalToken 来自详情的 pendingApprovals）
   */
  async function submitApproval(
    run: ChatRun,
    decisions: ApprovalDecisionReq[],
  ) {
    if (!run?.executionId) return;
    await submitWorkflow(
      { decisions, executionId: run.executionId },
      run.apiKey,
    );
    run.pendingApprovals = [];
    run.error = undefined;
    // 重新订阅同一 executionId：后续节点补写进同一个气泡
    subscribe(run.executionId, run);
  }

  /**
   * 在同一个 executionId 上重启一轮（restart）：提交后**新建**一个 ChatRun 并订阅。
   *
   * 多轮对话就靠它：会话 id 不变（大模型的会话记忆挂在 node_states 上，跨轮保留），
   * 但展示载体换成新气泡：往旧 run 上续写会把上一轮已经看完的回答整栏盖掉。
   * 不传 values 则沿用上一轮的行内输入。
   */
  async function retryRun(
    session: Pick<ChatRun, 'apiKey' | 'executionId'>,
    values?: Record<string, any>,
  ): Promise<ChatRun> {
    const { apiKey, executionId } = session;
    await submitWorkflow(
      { ...(values ? { values } : {}), executionId, restart: true },
      apiKey,
    );
    const next = createChatRun(executionId);
    next.apiKey = apiKey;
    subscribe(executionId, next);
    return next;
  }

  /** 主动取消后端执行（引擎在下一个检查点生效；PAUSED 下直接写终态） */
  async function cancelRun(run: ChatRun) {
    if (!run?.executionId) return;
    await cancelExecution(run.executionId, run.apiKey);
  }

  /** 主动停止（关闭连接并标记取消）：待审批状态下同样收流，面板随之收起 */
  function abort(run?: ChatRun) {
    if (run && (run.status === 'running' || run.status === 'paused')) {
      run.status = 'cancelled';
      // 停了就是放弃本轮审批，待办清单不清就会一直挂在气泡里
      run.pendingApprovals = [];
    }
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
