/**
 * 工作流调试状态 Store
 * 管理调试面板的状态，包括执行状态、节点追踪、审批挂起和变量等。
 *
 * 已随外部暂停链路废弃：断点（breakpoints，表列已删）与「暂停/恢复接口」。暂停的唯一来源
 * 是停在等人答：事件帧只报「停下了 + 哪个节点」，待办清单只从执行详情取（pendingApprovals）。
 */

import type {
  LlmToolKind,
  NodeType,
  PendingApproval,
} from '#/api/ai-workflow/types';
import type { WorkflowDebugErrorType as WorkflowDebugErrorTypeValue } from '#/views/wemirr/ai/workflow/domain/debug-errors';
import type { WorkflowRuntimeEventType } from '#/views/wemirr/ai/workflow/domain/runtime-events';

import { computed, ref } from 'vue';

import { defineStore } from 'pinia';

import { getExecution } from '#/api/ai-workflow';
import { WorkflowDebugErrorType } from '#/views/wemirr/ai/workflow/domain/debug-errors';

// ==================== 类型定义 ====================

/**
 * 节点执行状态
 * cancelled / timeout 为并行分支被砍后的终态（后端 node.cancelled / node.timeout）
 * skipped 为「再提交-恢复」本轮沿用的已完成节点
 * awaiting 为审批节点挂起等人工结论（非终态，恢复后必然重跑该节点）
 */
export type NodeExecutionStatus =
  | 'awaiting'
  | 'cancelled'
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'skipped'
  | 'timeout';

/**
 * 大模型节点插入工具后的一次调用（node.tool_call + node.tool_result 合并成一条）
 *
 * 两个事件靠 toolCallId 配对：只收到 call 时 result/error 为空，面板就画成「调用中」。
 */
export interface NodeToolCallTrace {
  /** 模型给的入参 */
  arguments?: Record<string, any>;
  /** 调用失败原因（后端同时把原文回填给模型，让它有机会改口） */
  error?: null | string;
  /** 工具来源 */
  kind?: LlmToolKind;
  /** 第几轮 tool-call（恢复轮补记的那次为 0） */
  round?: number;
  /** 结果摘要（完整结果在节点输出的 toolCalls 里） */
  result?: string;
  /** true = 子工作流审批结束后补记的那次调用 */
  resumed?: boolean;
  toolCallId?: string;
  /** 对外暴露的工具名（跨来源重名时会带来源前缀） */
  toolName?: string;
}

/**
 * 节点追踪数据
 */
export interface NodeTrace {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 节点类型 */
  nodeType: NodeType;
  /** 执行状态 */
  status: NodeExecutionStatus;
  /** 开始时间 */
  startTime: Date | null;
  /** 结束时间 */
  endTime: Date | null;
  /** 执行耗时(毫秒) */
  duration: null | number;
  /** 输入数据 */
  inputs: Record<string, any>;
  /** 输出数据 */
  outputs: Record<string, any>;
  /** 错误信息 */
  error?: {
    message: string;
    stackTrace?: string;
    type: string;
  };
  /** LLM 节点 Token 统计 */
  tokenUsage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
  /** HTTP 节点请求详情 */
  httpDetails?: {
    method: string;
    responseTime: number;
    statusCode: number;
    url: string;
  };
  /** 流式输出内容 */
  streamingContent?: string;
  /** 流式思维链内容（reasoning 增量，与正文分开累加） */
  streamingReasoning?: string;
  /** 插入工具后的调用过程（按事件到达顺序追加） */
  toolCalls?: NodeToolCallTrace[];
  /** 本轮未重跑（沿用上一轮输出） */
  skip?: boolean;
}

/**
 * 调试变量分组。
 */
export interface VariableGroup {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 变量列表 */
  variables: VariableItem[];
}

/**
 * 变量项
 */
export interface VariableItem {
  /** 变量名 */
  name: string;
  /** 变量值 */
  value: any;
  /** 变量类型 */
  type: string;
  /** 更新时间 */
  updatedAt?: Date;
}

/**
 * 执行时间线项
 */
export interface TimelineItem {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 节点类型 */
  nodeType: NodeType;
  /** 开始时间(相对于执行开始的毫秒数) */
  startTime: number;
  /** 结束时间 */
  endTime: number;
  /** 执行耗时 */
  duration: number;
  /** 执行状态 */
  status: NodeExecutionStatus;
}

/**
 * END 节点输出
 */
export interface EndNodeOutput {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 输出内容 */
  content: string;
  /** 内容类型 */
  contentType: 'image' | 'json' | 'markdown' | 'text';
}

/**
 * 执行结果
 */
export interface ExecutionResult {
  /** 执行状态 */
  status: 'completed' | 'failed';
  /** 输出数据 */
  outputs: Record<string, any>;
  /** 总执行时间(毫秒) */
  totalDuration: number;
  /** 总 Token 消耗 */
  totalTokens?: number;
  /** END 节点输出列表 */
  endNodeOutputs: EndNodeOutput[];
}

/**
 * SSE 事件类型
 */
export type DebugSSEEventType = WorkflowRuntimeEventType;

/**
 * SSE 事件数据
 */
export interface DebugSSEEvent {
  type: DebugSSEEventType;
  nodeId?: string;
  nodeName?: string;
  nodeType?: NodeType;
  input?: any;
  output?: any;
  error?: string;
  stackTrace?: string;
  token?: string;
  /** node.delta 增量是否属于思维链 */
  reasoning?: boolean;
  duration?: number;
  variables?: Record<string, any>;
  outputs?: Record<string, any>;
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
  /** node.timeout / node.cancelled：节点所属的并行分支 id */
  branchId?: string;
  /** node.completed：本轮未重跑（沿用上一轮结果） */
  skip?: boolean;
  /** node.tool_call / node.tool_result：模型侧调用 id（两个事件靠它配对） */
  toolCallId?: string;
  toolName?: string;
  toolKind?: LlmToolKind;
  /** node.tool_call：模型给的入参 */
  arguments?: Record<string, any>;
  /** node.tool_result：结果摘要 */
  result?: string;
  round?: number;
  resumed?: boolean;
}

/**
 * 调试状态接口
 */
export interface DebugState {
  /** 是否正在运行 */
  isRunning: boolean;
  /** 是否已暂停 */
  isPaused: boolean;
  /** 执行ID */
  executionId: null | string;
  /** 节点追踪数据 */
  nodeTraces: Map<string, NodeTrace>;
  /** 当前执行节点ID */
  currentNodeId: null | string;
  /** 变量数据 */
  variables: Map<string, any>;
  /** 执行结果 */
  result: ExecutionResult | null;
  /** 时间线数据 */
  timelineItems: TimelineItem[];
  /** 执行开始时间 */
  executionStartTime: Date | null;
  /** 错误历史记录*/
  errorHistory: ErrorHistoryItem[];
  /** 当前错误*/
  currentError: ErrorHistoryItem | null;
}

export type ErrorType = WorkflowDebugErrorTypeValue;

/**
 * 错误历史记录项
 */
export interface ErrorHistoryItem {
  /** 错误ID */
  id: string;
  /** 错误类型 */
  type: ErrorType;
  /** 错误消息 */
  message: string;
  /** 发生位置(节点ID) */
  nodeId?: string;
  /** 节点名称 */
  nodeName?: string;
  /** 堆栈跟踪 */
  stackTrace?: string;
  /** 修复建议 */
  suggestions?: string[];
  /** 发生时间 */
  timestamp: Date;
  /** 是否可重试 */
  retryable: boolean;
  /** 相关上下文 */
  context?: Record<string, any>;
  /** 是否已解决 */
  resolved: boolean;
  /** 执行ID */
  executionId?: string;
}

// ==================== 常量定义 ====================

/** 最大错误历史记录数 */
const MAX_ERROR_HISTORY_SIZE = 50;

/** 生成唯一ID */
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

// ==================== Store 定义 ====================

export const useDebugStore = defineStore('debug', () => {
  // ==================== 状态 ====================

  /** 是否正在运行 */
  const isRunning = ref(false);

  /** 是否已暂停 */
  const isPaused = ref(false);

  /** 执行ID */
  const executionId = ref<null | string>(null);

  /** 节点追踪数据 */
  const nodeTraces = ref<Map<string, NodeTrace>>(new Map());

  /** 当前执行节点ID */
  const currentNodeId = ref<null | string>(null);

  /**
   * 此刻欠人答的审批（执行详情 pendingApprovals 的镜像，审批面板的唯一数据源）
   * 只从详情读一次，不从帧里拼：同一个 executionId 的任何后续动作都以它为准
   */
  const pendingApprovals = ref<PendingApproval[]>([]);

  /** 变量数据 */
  const variables = ref<Map<string, any>>(new Map());

  /** 执行结果 */
  const result = ref<ExecutionResult | null>(null);

  /** 时间线数据 */
  const timelineItems = ref<TimelineItem[]>([]);

  /** 执行开始时间 */
  const executionStartTime = ref<Date | null>(null);

  /** 错误历史记录*/
  const errorHistory = ref<ErrorHistoryItem[]>([]);

  /** 当前错误*/
  const currentError = ref<ErrorHistoryItem | null>(null);

  // ==================== 计算属性 ====================

  /** 欠人答的审批清单（并行下可能多份） */
  const awaitingApprovalList = computed(() => pendingApprovals.value);

  /**
   * 是不是真的在等人答：空清单意味着 PAUSED 另有原因（已交给子执行跑），
   * 再弹一次审批面板就是把同一份结论问第二遍
   */
  const isAwaitingApproval = computed(() => pendingApprovals.value.length > 0);

  /** 节点追踪列表(按开始时间排序) */
  const sortedNodeTraces = computed(() =>
    [...nodeTraces.value.values()]
      .filter((trace) => trace.startTime !== null)
      .sort(
        (a, b) => (a.startTime?.getTime() || 0) - (b.startTime?.getTime() || 0),
      ),
  );

  /** 变量分组列表 */
  const variableGroups = computed((): VariableGroup[] => {
    const groups: Map<string, VariableGroup> = new Map();

    nodeTraces.value.forEach((trace) => {
      if (trace.status === 'completed' && trace.outputs) {
        const variableItems: VariableItem[] = Object.entries(trace.outputs).map(
          ([name, value]) => ({
            name,
            value,
            type: typeof value,
            updatedAt: trace.endTime || undefined,
          }),
        );

        // 流式节点的 node.completed 不广播 output（内容由 node.delta 送达）：
        // 用 delta 累积值补齐，键名与后端 output 的 text/reasoning 对齐
        if (variableItems.length === 0) {
          if (trace.streamingContent) {
            variableItems.push({
              name: 'text',
              value: trace.streamingContent,
              type: 'string',
              updatedAt: trace.endTime || undefined,
            });
          }
          if (trace.streamingReasoning) {
            variableItems.push({
              name: 'reasoning',
              value: trace.streamingReasoning,
              type: 'string',
              updatedAt: trace.endTime || undefined,
            });
          }
        }

        if (variableItems.length > 0) {
          groups.set(trace.nodeId, {
            nodeId: trace.nodeId,
            nodeName: trace.nodeName,
            variables: variableItems,
          });
        }
      }
    });

    return [...groups.values()];
  });

  /** 总执行时间 */
  const totalDuration = computed(() => {
    if (!executionStartTime.value) return 0;
    const endTime = result.value ? new Date() : new Date();
    return endTime.getTime() - executionStartTime.value.getTime();
  });

  /** 总 Token 消耗 */
  const totalTokens = computed(() => {
    let total = 0;
    nodeTraces.value.forEach((trace) => {
      if (trace.tokenUsage) {
        total += trace.tokenUsage.totalTokens;
      }
    });
    return total;
  });

  /** 是否有当前错误*/
  const hasCurrentError = computed(() => currentError.value !== null);

  /** 错误历史数量*/
  const errorHistoryCount = computed(() => errorHistory.value.length);

  /** 未解决的错误数量*/
  const unresolvedErrorCount = computed(
    () => errorHistory.value.filter((e) => !e.resolved).length,
  );

  // ==================== 执行状态管理 ====================

  /**
   * 开始预览运行
   */
  function startPreviewRun(execId: string) {
    isRunning.value = true;
    isPaused.value = false;
    executionId.value = execId;
    executionStartTime.value = new Date();
    nodeTraces.value.clear();
    currentNodeId.value = null;
    result.value = null;
    timelineItems.value = [];
    clearPendingApprovals();
  }

  /**
   * 节点停在等人答（只改节点追踪与当前节点，不存审批内容）
   * @param nodeId 正挂着的节点 id（等子流程时就是那个【工作流】节点）
   */
  function markNodeAwaiting(nodeId: string) {
    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      trace.status = 'awaiting';
      trace.endTime = new Date();
    }
    currentNodeId.value = nodeId;
  }

  /**
   * 已提交审批结论（或新一轮执行开始）：清掉待办清单，面板收起审批表单
   */
  function clearPendingApprovals() {
    pendingApprovals.value = [];
  }

  /**
   * 暂停执行（只有审批节点会造成，语义 = 等待人工审批）
   */
  function pauseExecution() {
    isPaused.value = true;
  }

  /**
   * 提交后恢复推进（清审批残留 + 回到执行中）
   *
   * isRunning 必须一起置回：恢复提交沿用同一个连接继续跑，运行态不落回 true
   * 就会让面板在整段续跑期间既没有「执行中」标识、也不拦重复的预览运行。
   */
  function resumeExecution() {
    isPaused.value = false;
    isRunning.value = true;
    clearPendingApprovals();
  }

  /**
   * 取消执行
   */
  function cancelExecution() {
    isRunning.value = false;
    isPaused.value = false;
  }

  /**
   * 完成执行
   */
  function completeExecution(outputs: Record<string, any>, duration: number) {
    isRunning.value = false;
    isPaused.value = false;
    currentNodeId.value = null;

    // 构建执行结果
    result.value = {
      status: 'completed',
      outputs,
      totalDuration: duration,
      totalTokens: totalTokens.value,
      endNodeOutputs: buildEndNodeOutputs(outputs),
    };
  }

  /**
   * 执行失败
   */
  function failExecution(_error: string) {
    isRunning.value = false;
    isPaused.value = false;

    result.value = {
      status: 'failed',
      outputs: {},
      totalDuration: totalDuration.value,
      totalTokens: totalTokens.value,
      endNodeOutputs: [],
    };
  }

  /**
   * 构建 END 节点输出
   */
  function buildEndNodeOutputs(_outputs: Record<string, any>): EndNodeOutput[] {
    const endOutputs: EndNodeOutput[] = [];

    // 查找 END 节点的追踪数据
    nodeTraces.value.forEach((trace) => {
      if (trace.nodeType === 'END' && trace.status === 'completed') {
        const content = JSON.stringify(trace.outputs, null, 2);
        endOutputs.push({
          nodeId: trace.nodeId,
          nodeName: trace.nodeName,
          content,
          contentType: detectContentType(content),
        });
      }
    });

    return endOutputs;
  }

  /**
   * 检测内容类型
   */
  function detectContentType(
    content: string,
  ): 'image' | 'json' | 'markdown' | 'text' {
    if (
      content.startsWith('http') &&
      /\.(png|jpg|jpeg|gif|webp)$/i.test(content)
    ) {
      return 'image';
    }
    if (
      content.includes('```') ||
      content.includes('# ') ||
      content.includes('**')
    ) {
      return 'markdown';
    }
    try {
      JSON.parse(content);
      return 'json';
    } catch {
      return 'text';
    }
  }

  // ==================== 节点追踪管理 ====================

  /** 节点名称解析器函数 */
  let nodeNameResolver: ((nodeId: string) => string) | null = null;

  /**
   * 设置节点名称解析器
   * 用于从画布获取节点的友好名称
   */
  function setNodeNameResolver(resolver: (nodeId: string) => string) {
    nodeNameResolver = resolver;
  }

  /**
   * 获取节点友好名称
   * 优先使用 SSE 事件中的 nodeName，否则使用解析器，最后回退到 nodeId
   */
  function resolveNodeName(nodeId: string, eventNodeName?: string): string {
    // 优先使用事件中的名称（如果不是 UUID 格式）
    if (eventNodeName && !isUUID(eventNodeName)) {
      return eventNodeName;
    }
    // 使用解析器获取名称
    if (nodeNameResolver) {
      const resolvedName = nodeNameResolver(nodeId);
      if (resolvedName && resolvedName !== nodeId) {
        return resolvedName;
      }
    }
    // 回退到 nodeId
    return eventNodeName || nodeId;
  }

  /**
   * 检查字符串是否为 UUID 格式
   */
  function isUUID(str: string): boolean {
    const uuidRegex =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    return uuidRegex.test(str);
  }

  /**
   * 处理节点开始事件
   */
  function handleNodeStart(event: DebugSSEEvent) {
    const { nodeId, nodeName, nodeType, input } = event;
    if (!nodeId) return;

    // 解析节点友好名称
    const friendlyName = resolveNodeName(nodeId, nodeName);

    const trace: NodeTrace = {
      nodeId,
      nodeName: friendlyName,
      nodeType: nodeType || 'START',
      status: 'running',
      startTime: new Date(),
      endTime: null,
      duration: null,
      inputs: input || {},
      outputs: {},
    };

    nodeTraces.value.set(nodeId, trace);
    currentNodeId.value = nodeId;

    // 添加到时间线
    if (executionStartTime.value) {
      const startOffset =
        trace.startTime!.getTime() - executionStartTime.value.getTime();
      timelineItems.value.push({
        nodeId,
        nodeName: friendlyName,
        nodeType: trace.nodeType,
        startTime: startOffset,
        endTime: startOffset,
        duration: 0,
        status: 'running',
      });
    }
  }

  /**
   * 处理节点完成事件
   */
  function handleNodeComplete(event: DebugSSEEvent) {
    const { nodeId, output, duration, tokenUsage, httpDetails, skip } = event;
    if (!nodeId) return;

    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      // skip：恢复提交里本轮沿用的上轮已完成节点——不能画成正常完成，
      // 否则耗时看起来像 0ms 的真跑，也看不出「这一轮没重跑」
      trace.status = skip ? 'skipped' : 'completed';
      trace.skip = Boolean(skip);
      trace.endTime = new Date();
      // 0ms（轻量节点）也是有效耗时，不能当作“无数据”清掉
      trace.duration = duration ?? null;
      trace.outputs = output || {};
      trace.tokenUsage = tokenUsage;
      trace.httpDetails = httpDetails;

      // 更新时间线
      updateTimelineItem(nodeId, trace.status, duration ?? 0);
    }
  }

  /**
   * 处理节点错误事件
   */
  function handleNodeError(event: DebugSSEEvent) {
    const { nodeId, error, stackTrace } = event;
    if (!nodeId) return;

    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      trace.status = 'failed';
      trace.endTime = new Date();
      trace.error = {
        type: 'ExecutionError',
        message: error || 'Unknown error',
        stackTrace,
      };

      // 更新时间线
      const duration = trace.startTime
        ? Date.now() - trace.startTime.getTime()
        : 0;
      updateTimelineItem(nodeId, 'failed', duration);
    }
  }

  /**
   * 节点中止收敛（node.timeout / node.cancelled 共用）
   *
   * 并行屏障「任一完成 + 等待超时」下被砍的分支节点，后端在 node.started 之后
   * 会补发一个终态事件；不接这个终态，画布与节点追踪会一直停在蓝色「执行中」。
   * timeout=黄色「已超时」（时限到点），cancelled=灰色「已取消」（其他分支先完成）。
   */
  function markNodeAborted(event: DebugSSEEvent, status: NodeExecutionStatus) {
    const { nodeId, duration, error } = event;
    if (!nodeId) return;

    const trace = nodeTraces.value.get(nodeId);
    // 已出终态（跑完了/失败了）的节点不被后到的中止事件覆盖
    if (!trace || trace.status === 'completed' || trace.status === 'failed') {
      return;
    }

    trace.status = status;
    trace.endTime = new Date();
    trace.duration = duration ?? null;
    trace.error = {
      type: status === 'timeout' ? 'TIMEOUT' : 'CANCELLED',
      message:
        error ||
        (status === 'timeout'
          ? '并行分支等待超时，已停止等待'
          : '并行分支已取消'),
    };

    updateTimelineItem(nodeId, status, duration ?? 0);
  }

  /**
   * 处理节点超时事件（并行分支等待时限到点）
   */
  function handleNodeTimeout(event: DebugSSEEvent) {
    markNodeAborted(event, 'timeout');
  }

  /**
   * 处理节点取消事件（并行分支被其他先完成的分支短路）
   */
  function handleNodeCancelled(event: DebugSSEEvent) {
    markNodeAborted(event, 'cancelled');
  }

  /**
   * 处理流式 Token 事件
   */
  function handleStreamingToken(event: DebugSSEEvent) {
    const { nodeId, token, reasoning } = event;
    if (!nodeId || !token) return;

    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      if (reasoning) {
        trace.streamingReasoning = (trace.streamingReasoning || '') + token;
      } else {
        trace.streamingContent = (trace.streamingContent || '') + token;
      }
    }
  }

  /**
   * 处理工具调用事件（node.tool_call / node.tool_result）
   *
   * 同一节点的多轮多次调用按到达顺序累加到 trace.toolCalls；结果帧靠 toolCallId
   * 找回它对应的那一条（模型一轮可并行发多个调用，不能简单假定是最后一条）。
   */
  function handleToolEvent(event: DebugSSEEvent) {
    const { nodeId, toolCallId } = event;
    if (!nodeId) return;
    const trace = nodeTraces.value.get(nodeId);
    if (!trace) return;

    if (event.type === 'node.tool_call') {
      trace.toolCalls = [
        ...(trace.toolCalls || []),
        {
          arguments: event.arguments,
          kind: event.toolKind,
          round: event.round,
          resumed: event.resumed,
          toolCallId,
          toolName: event.toolName,
        },
      ];
      return;
    }

    const existed = (trace.toolCalls || []).find(
      (item) => item.toolCallId && item.toolCallId === toolCallId,
    );
    if (existed) {
      existed.error = event.error ?? null;
      existed.result = event.result;
      if (event.round !== undefined) existed.round = event.round;
      if (event.resumed !== undefined) existed.resumed = event.resumed;
      if (!existed.toolName && event.toolName) {
        existed.toolName = event.toolName;
      }
      return;
    }
    // 只收到结果帧（调用帧丢了 / 订阅晚于该节点开始）：也得可见，不然过程看起来没调过工具
    trace.toolCalls = [
      ...(trace.toolCalls || []),
      {
        error: event.error ?? null,
        kind: event.toolKind,
        result: event.result,
        round: event.round,
        resumed: event.resumed,
        toolCallId,
        toolName: event.toolName,
      },
    ];
  }

  /**
   * 读一次执行详情，把本地待办清单刷成它给的那份
   *
   * 帧不承载审批内容，一份待办只有详情一个出处：收到暂停帧、连接掉线都回到这里取。
   * @returns 行此刻的状态（RUNNING / PAUSED / 终态），取不到时返回空串
   */
  async function refreshPendingApprovals(): Promise<string> {
    const execId = executionId.value;
    if (!execId) return '';
    try {
      const detail = await getExecution(execId);
      pendingApprovals.value = detail.pendingApprovals || [];
      pendingApprovals.value.forEach((item) => markNodeAwaiting(item.nodeId));
      return detail.status || '';
    } catch {
      // 详情读不到就不猜：面板维持现状，用户重试或刷新
      return '';
    }
  }

  /**
   * 处理节点级挂起事件（node.paused）
   *
   * 只把那个节点画成「等人答」，不改 isRunning/isPaused：本轮还会继续跑无关分支，
   * 整条流停下来由随后的 workflow.paused（或同步接口的收尾帧）确认。
   */
  function handleNodePaused(event: DebugSSEEvent) {
    if (!event.nodeId) return;
    markNodeAwaiting(event.nodeId);
    void refreshPendingApprovals();
  }

  /**
   * 本轮已停下来但没收到暂停/终态帧时的收尾（同步接口返回 / 连接断开）：
   * 以详情为准——还真欠人答就落回「暂停等审批」，已跑完则不动（终态帧自己会复位）。
   */
  async function settleAwaitingRun() {
    if (!isRunning.value) return;
    if ((await refreshPendingApprovals()) !== 'PAUSED') return;
    isRunning.value = false;
    isPaused.value = true;
  }

  /**
   * 处理执行暂停事件（workflow.paused：本轮跑完了，但有节点停在等人答）
   */
  function handleApprovalPaused(event: DebugSSEEvent) {
    isPaused.value = true;
    // 本轮已收尾（引擎不会再推节点事件），运行态不清就会一直转圈：
    // 停止按钮、新的运行、继续运行全部卡在 isRunning 上
    isRunning.value = false;
    if (event.nodeId) markNodeAwaiting(event.nodeId);

    // 更新变量（暂停时的全局变量快照，仅供排障展示）
    if (event.variables) {
      Object.entries(event.variables).forEach(([key, value]) => {
        variables.value.set(key, value);
      });
    }
    void refreshPendingApprovals();
  }

  /**
   * 更新时间线项
   */
  function updateTimelineItem(
    nodeId: string,
    status: NodeExecutionStatus,
    duration: number,
  ) {
    const item = timelineItems.value.find((i) => i.nodeId === nodeId);
    if (item) {
      item.status = status;
      item.duration = duration;
      item.endTime = item.startTime + duration;
    }
  }

  /**
   * 获取节点追踪数据
   */
  function getNodeTrace(nodeId: string): NodeTrace | undefined {
    return nodeTraces.value.get(nodeId);
  }

  // ==================== 变量管理 ====================

  /**
   * 设置变量值
   */
  function setVariable(name: string, value: any) {
    variables.value.set(name, value);
  }

  /**
   * 获取变量值
   */
  function getVariable(name: string): any {
    return variables.value.get(name);
  }

  /**
   * 批量更新变量
   */
  function updateVariables(newVariables: Record<string, any>) {
    Object.entries(newVariables).forEach(([key, value]) => {
      variables.value.set(key, value);
    });
  }

  /**
   * 清除所有变量
   */
  function clearVariables() {
    variables.value.clear();
  }

  // ==================== 错误历史管理 ====================

  /**
   * 加载错误历史。
   */
  function loadErrorHistory(workflowId: string) {
    void workflowId;
    errorHistory.value = [];
  }

  /**
   * 添加错误到历史记录
   */
  function addErrorToHistory(
    workflowId: string,
    error: Omit<ErrorHistoryItem, 'id' | 'resolved' | 'timestamp'>,
  ): ErrorHistoryItem {
    const errorItem: ErrorHistoryItem = {
      ...error,
      id: generateId(),
      timestamp: new Date(),
      resolved: false,
      executionId: executionId.value || undefined,
    };

    // 添加到历史记录开头
    errorHistory.value.unshift(errorItem);

    // 限制历史记录数量
    if (errorHistory.value.length > MAX_ERROR_HISTORY_SIZE) {
      errorHistory.value = errorHistory.value.slice(0, MAX_ERROR_HISTORY_SIZE);
    }

    // 设置为当前错误
    currentError.value = errorItem;

    void workflowId;

    return errorItem;
  }

  /**
   * 从节点追踪创建错误记录
   */
  function createErrorFromNodeTrace(
    workflowId: string,
    trace: NodeTrace,
  ): ErrorHistoryItem | null {
    if (!trace.error) return null;

    return addErrorToHistory(workflowId, {
      type: WorkflowDebugErrorType.EXECUTION,
      message: trace.error.message,
      nodeId: trace.nodeId,
      nodeName: trace.nodeName,
      stackTrace: trace.error.stackTrace,
      retryable: true,
      suggestions: getErrorSuggestions(trace.error.type, trace.nodeType),
      context: {
        nodeType: trace.nodeType,
        inputs: trace.inputs,
      },
    });
  }

  /**
   * 获取错误修复建议
   */
  function getErrorSuggestions(
    errorType: string,
    nodeType: NodeType,
  ): string[] {
    const suggestions: string[] = [];

    // 根据节点类型提供建议
    switch (nodeType) {
      case 'CODE': {
        suggestions.push(
          '检查代码语法是否正确',
          '确认输入变量是否存在',
          '检查代码逻辑是否有错误',
        );
        break;
      }
      case 'HTTP_REQUEST': {
        suggestions.push(
          '检查请求 URL 是否正确',
          '确认请求参数格式是否正确',
          '检查目标服务是否可用',
        );
        break;
      }
      case 'KNOWLEDGE_RETRIEVAL': {
        suggestions.push('检查知识库配置是否正确', '确认知识库是否可访问');
        break;
      }
      case 'LLM': {
        suggestions.push(
          '检查 LLM 模型配置是否正确',
          '确认 API 密钥是否有效',
          '检查输入提示词是否符合要求',
        );
        break;
      }
      case 'MCP_TOOL': {
        suggestions.push(
          '检查 MCP 连接是否正常（可在「智能体 - MCP」页测试连接）',
          '确认工具入参已绑定上游变量或自定义值',
          '查看工具返回内容：MCP 侧报错（isError）会直接判定节点失败',
        );
        break;
      }
      case 'TOOL': {
        suggestions.push(
          '确认工具在「智能体 - 工具」页处于启用状态',
          '检查参数绑定是否指向已存在的上游变量',
          '适当加大超时时间，或先到工具页用「测试」跑一次代码',
        );
        break;
      }
      case 'WORKFLOW': {
        suggestions.push(
          '确认子工作流仍处于已发布状态，且那个版本没被作废（选「指定版本」时跑的是快照）',
          '确认执行用 API Key 仍启用且未过期：子工作流的「API Key」页可查状态与限流额度',
          '节点失败原因里带了子执行 id，拿它到执行历史里看子流程哪个节点挂了',
          '子流程里有审批节点时本节点会跟着停在「待审批」，结论要提交给子执行',
        );
        break;
      }
      default: {
        suggestions.push(
          '检查节点配置是否正确',
          '确认输入数据格式是否符合要求',
        );
      }
    }

    // 根据错误类型添加通用建议
    if (errorType.includes('timeout') || errorType.includes('Timeout')) {
      suggestions.push('尝试增加超时时间', '检查网络连接是否稳定');
    }

    if (errorType.includes('permission') || errorType.includes('Permission')) {
      suggestions.push('检查是否有相应的访问权限');
    }

    return suggestions;
  }

  /**
   * 标记错误为已解决
   */
  function resolveError(workflowId: string, errorId: string) {
    const error = errorHistory.value.find((e) => e.id === errorId);
    if (error) {
      error.resolved = true;

      // 如果是当前错误，清除它
      if (currentError.value?.id === errorId) {
        currentError.value = null;
      }

      void workflowId;
    }
  }

  /**
   * 清除当前错误
   */
  function clearCurrentError() {
    currentError.value = null;
  }

  /**
   * 删除错误历史记录
   */
  function deleteErrorFromHistory(workflowId: string, errorId: string) {
    const index = errorHistory.value.findIndex((e) => e.id === errorId);
    if (index !== -1) {
      errorHistory.value.splice(index, 1);

      // 如果是当前错误，清除它
      if (currentError.value?.id === errorId) {
        currentError.value = null;
      }

      void workflowId;
    }
  }

  /**
   * 清除所有错误历史
   */
  function clearErrorHistory(workflowId: string) {
    errorHistory.value = [];
    currentError.value = null;
    void workflowId;
  }

  /**
   * 获取错误详情
   */
  function getErrorById(errorId: string): ErrorHistoryItem | undefined {
    return errorHistory.value.find((e) => e.id === errorId);
  }

  /**
   * 处理 SSE 事件(通用入口)
   */
  function handleSSEEvent(event: DebugSSEEvent) {
    switch (event.type) {
      case 'node.cancelled': {
        handleNodeCancelled(event);
        break;
      }
      case 'node.completed': {
        handleNodeComplete(event);
        break;
      }
      case 'node.delta': {
        handleStreamingToken(event);
        break;
      }
      case 'node.failed': {
        handleNodeError(event);
        break;
      }
      case 'node.paused': {
        handleNodePaused(event);
        break;
      }
      case 'node.started': {
        handleNodeStart(event);
        break;
      }
      case 'node.timeout': {
        handleNodeTimeout(event);
        break;
      }
      case 'node.tool_call':
      case 'node.tool_result': {
        handleToolEvent(event);
        break;
      }
      case 'workflow.cancelled': {
        cancelExecution();
        break;
      }
      case 'workflow.completed': {
        completeExecution(event.outputs || {}, event.duration || 0);
        break;
      }
      case 'workflow.failed': {
        failExecution(event.error || 'Execution failed');
        break;
      }
      case 'workflow.paused': {
        handleApprovalPaused(event);
        break;
      }
      case 'workflow.resumed': {
        // 恢复提交的首帧：本轮从 START 重进，上一轮的挂起登记必须失效
        resumeExecution();
        break;
      }
    }
  }

  // ==================== 重置 ====================

  /**
   * 重置所有状态
   */
  function $reset() {
    isRunning.value = false;
    isPaused.value = false;
    executionId.value = null;
    nodeTraces.value.clear();
    currentNodeId.value = null;
    clearPendingApprovals();
    variables.value.clear();
    result.value = null;
    timelineItems.value = [];
    executionStartTime.value = null;
    errorHistory.value = [];
    currentError.value = null;
  }

  /**
   * 清除执行状态(保留错误历史)
   */
  function clearExecutionState() {
    isRunning.value = false;
    isPaused.value = false;
    executionId.value = null;
    nodeTraces.value.clear();
    currentNodeId.value = null;
    clearPendingApprovals();
    variables.value.clear();
    result.value = null;
    timelineItems.value = [];
    executionStartTime.value = null;
    currentError.value = null;
  }

  // ==================== 返回 ====================

  return {
    // 状态
    isRunning,
    isPaused,
    executionId,
    nodeTraces,
    currentNodeId,
    pendingApprovals,
    variables,
    result,
    timelineItems,
    executionStartTime,
    errorHistory,
    currentError,

    // 计算属性
    awaitingApprovalList,
    isAwaitingApproval,
    sortedNodeTraces,
    variableGroups,
    totalDuration,
    totalTokens,
    hasCurrentError,
    errorHistoryCount,
    unresolvedErrorCount,

    // 执行状态管理
    startPreviewRun,
    pauseExecution,
    resumeExecution,
    cancelExecution,
    completeExecution,
    failExecution,

    // 节点追踪管理
    handleNodeStart,
    handleNodeComplete,
    handleNodeError,
    handleNodeTimeout,
    handleNodeCancelled,
    handleStreamingToken,
    handleToolEvent,
    handleNodePaused,
    handleApprovalPaused,
    markNodeAwaiting,
    clearPendingApprovals,
    refreshPendingApprovals,
    settleAwaitingRun,
    getNodeTrace,
    setNodeNameResolver,

    // 变量管理
    setVariable,
    getVariable,
    updateVariables,
    clearVariables,

    // 错误历史管理
    loadErrorHistory,
    addErrorToHistory,
    createErrorFromNodeTrace,
    resolveError,
    clearCurrentError,
    deleteErrorFromHistory,
    clearErrorHistory,
    getErrorById,

    // SSE 事件处理
    handleSSEEvent,

    // 重置
    $reset,
    clearExecutionState,
  };
});
