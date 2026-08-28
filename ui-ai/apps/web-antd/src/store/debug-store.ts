/**
 * 工作流调试状态 Store
 * 管理调试面板的状态，包括执行状态、节点追踪、检查点和变量等。
 *
 */

import type { NodeType } from '#/api/ai-workflow/types';
import type { WorkflowDebugErrorType as WorkflowDebugErrorTypeValue } from '#/views/wemirr/ai/workflow/domain/debug-errors';
import type { WorkflowRuntimeEventType } from '#/views/wemirr/ai/workflow/domain/runtime-events';

import { computed, ref } from 'vue';

import { defineStore } from 'pinia';

import { WorkflowDebugErrorType } from '#/views/wemirr/ai/workflow/domain/debug-errors';

// ==================== 类型定义 ====================

/**
 * 节点执行状态
 */
export type NodeExecutionStatus =
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'skipped';

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
}

/**
 * 检查点信息
 */
export interface Breakpoint {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 节点类型 */
  nodeType: NodeType;
  /** 是否启用 */
  enabled: boolean;
  /** 条件表达式(可选) */
  condition?: string;
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
  /** 断点集合 */
  breakpoints: Map<string, Breakpoint>;
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

  /** 断点集合 */
  const breakpoints = ref<Map<string, Breakpoint>>(new Map());

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

  /** 断点数量 */
  const breakpointCount = computed(() => breakpoints.value.size);

  /** 启用的断点列表 */
  const enabledBreakpoints = computed(() =>
    [...breakpoints.value.values()].filter((bp) => bp.enabled),
  );

  /** 启用的断点ID列表 */
  const enabledBreakpointIds = computed(() =>
    enabledBreakpoints.value.map((bp) => bp.nodeId),
  );

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
  }

  /**
   * 暂停执行
   */
  function pauseExecution() {
    isPaused.value = true;
  }

  /**
   * 恢复执行
   */
  function resumeExecution() {
    isPaused.value = false;
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
    const { nodeId, output, duration, tokenUsage, httpDetails } = event;
    if (!nodeId) return;

    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      trace.status = 'completed';
      trace.endTime = new Date();
      trace.duration = duration || null;
      trace.outputs = output || {};
      trace.tokenUsage = tokenUsage;
      trace.httpDetails = httpDetails;

      // 更新时间线
      updateTimelineItem(nodeId, 'completed', duration || 0);
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
   * 处理流式 Token 事件
   */
  function handleStreamingToken(event: DebugSSEEvent) {
    const { nodeId, token } = event;
    if (!nodeId || !token) return;

    const trace = nodeTraces.value.get(nodeId);
    if (trace) {
      trace.streamingContent = (trace.streamingContent || '') + token;
    }
  }

  /**
   * 处理断点命中事件
   */
  function handleBreakpointHit(event: DebugSSEEvent) {
    const { nodeId, variables: eventVariables } = event;
    if (!nodeId) return;

    isPaused.value = true;
    currentNodeId.value = nodeId;

    // 更新变量
    if (eventVariables) {
      Object.entries(eventVariables).forEach(([key, value]) => {
        variables.value.set(key, value);
      });
    }
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

  // ==================== 断点管理 ====================

  /**
   * 添加断点
   */
  function addBreakpoint(
    nodeId: string,
    nodeName: string,
    nodeType: NodeType,
    condition?: string,
  ) {
    breakpoints.value.set(nodeId, {
      nodeId,
      nodeName,
      nodeType,
      enabled: true,
      condition,
    });
  }

  /**
   * 移除断点
   */
  function removeBreakpoint(nodeId: string) {
    breakpoints.value.delete(nodeId);
  }

  /**
   * 切换断点
   */
  function toggleBreakpoint(
    nodeId: string,
    nodeName: string,
    nodeType: NodeType,
  ) {
    if (breakpoints.value.has(nodeId)) {
      removeBreakpoint(nodeId);
    } else {
      addBreakpoint(nodeId, nodeName, nodeType);
    }
  }

  /**
   * 启用/禁用断点
   */
  function setBreakpointEnabled(nodeId: string, enabled: boolean) {
    const bp = breakpoints.value.get(nodeId);
    if (bp) {
      bp.enabled = enabled;
    }
  }

  /**
   * 设置条件检查点
   */
  function setBreakpointCondition(nodeId: string, condition: string) {
    const bp = breakpoints.value.get(nodeId);
    if (bp) {
      bp.condition = condition;
    }
  }

  /**
   * 清除所有断点
   */
  function clearAllBreakpoints() {
    breakpoints.value.clear();
  }

  /**
   * 启用所有断点
   */
  function enableAllBreakpoints() {
    breakpoints.value.forEach((bp) => {
      bp.enabled = true;
    });
  }

  /**
   * 禁用所有断点
   */
  function disableAllBreakpoints() {
    breakpoints.value.forEach((bp) => {
      bp.enabled = false;
    });
  }

  /**
   * 检查节点是否有断点
   */
  function hasBreakpoint(nodeId: string): boolean {
    return breakpoints.value.has(nodeId);
  }

  /**
   * 检查节点断点是否启用
   */
  function isBreakpointEnabled(nodeId: string): boolean {
    const bp = breakpoints.value.get(nodeId);
    return bp?.enabled ?? false;
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
      case 'node.started': {
        handleNodeStart(event);
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
        handleBreakpointHit(event);
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
    breakpoints.value.clear();
    variables.value.clear();
    result.value = null;
    timelineItems.value = [];
    executionStartTime.value = null;
    errorHistory.value = [];
    currentError.value = null;
  }

  /**
   * 清除执行状态(保留断点和错误历史)
   */
  function clearExecutionState() {
    isRunning.value = false;
    isPaused.value = false;
    executionId.value = null;
    nodeTraces.value.clear();
    currentNodeId.value = null;
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
    breakpoints,
    variables,
    result,
    timelineItems,
    executionStartTime,
    errorHistory,
    currentError,

    // 计算属性
    breakpointCount,
    enabledBreakpoints,
    enabledBreakpointIds,
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
    handleStreamingToken,
    handleBreakpointHit,
    getNodeTrace,
    setNodeNameResolver,

    // 断点管理
    addBreakpoint,
    removeBreakpoint,
    toggleBreakpoint,
    setBreakpointEnabled,
    setBreakpointCondition,
    clearAllBreakpoints,
    enableAllBreakpoints,
    disableAllBreakpoints,
    hasBreakpoint,
    isBreakpointEnabled,

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
