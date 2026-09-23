import type {
  ExecutionEventType,
  LlmToolKind,
  NodeType,
} from '#/api/ai-workflow/types';

import { WORKFLOW_RUNTIME_EVENT_TYPES } from '#/api/ai-workflow/types';

export { WORKFLOW_RUNTIME_EVENT_TYPES };

export type WorkflowRuntimeEventType = ExecutionEventType;

export interface WorkflowRuntimeEventBase {
  type: WorkflowRuntimeEventType;
  executionId?: string;
  timestamp?: number;
  /** 发出这一帧时那一轮的挂起代次；迟到的旧轮帧靠它丢弃 */
  pauseGeneration?: number;
}

export interface WorkflowStartedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.started';
  workflowId?: number | string;
  inputs?: Record<string, unknown>;
}

export interface WorkflowResumedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.resumed';
}

export interface NodeStartedEvent extends WorkflowRuntimeEventBase {
  type: 'node.started';
  nodeId: string;
  nodeName?: string;
  nodeType?: NodeType;
  input?: unknown;
}

export interface NodeDeltaEvent extends WorkflowRuntimeEventBase {
  type: 'node.delta';
  nodeId: string;
  token: string;
  /** true 表示该增量属于思维链(reasoning)，false/缺省为正文 */
  reasoning?: boolean;
}

export interface NodeCompletedEvent extends WorkflowRuntimeEventBase {
  type: 'node.completed';
  nodeId: string;
  output?: unknown;
  duration?: number;
  /** true = 本轮未重跑（恢复提交里沿用上一轮结果） */
  skip?: boolean;
  tokenUsage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
}

export interface NodeFailedEvent extends WorkflowRuntimeEventBase {
  type: 'node.failed';
  nodeId: string;
  error: string;
  stackTrace?: string;
}

/** 并行分支等待超时被停止的节点（超时策略产生的终态，非失败） */
export interface NodeTimeoutEvent extends WorkflowRuntimeEventBase {
  type: 'node.timeout';
  nodeId: string;
  /** 所属并行分支 id */
  branchId?: string;
  duration?: number;
  error?: string;
}

/** 并行分支被其他分支先完成短路的节点（非失败） */
export interface NodeCancelledEvent extends WorkflowRuntimeEventBase {
  type: 'node.cancelled';
  nodeId: string;
  /** 所属并行分支 id */
  branchId?: string;
  duration?: number;
  error?: string;
}

/** 大模型节点插入工具后的一次调用（模型要求调哪个工具、传了什么参数） */
export interface NodeToolCallEvent extends WorkflowRuntimeEventBase {
  type: 'node.tool_call';
  nodeId: string;
  toolCallId?: string;
  toolName?: string;
  toolKind?: LlmToolKind;
  arguments?: Record<string, unknown>;
  round?: number;
  /** true = 子工作流审批结束后补记的那次调用 */
  resumed?: boolean;
}

/** 一次工具调用的结果摘要（完整结果在节点输出的 toolCalls 里） */
export interface NodeToolResultEvent extends WorkflowRuntimeEventBase {
  type: 'node.tool_result';
  nodeId: string;
  toolCallId?: string;
  toolName?: string;
  toolKind?: LlmToolKind;
  result?: string;
  error?: null | string;
  round?: number;
  resumed?: boolean;
}

/**
 * 审批节点挂起（节点停在 AWAITING，本分支终止不路由下游）
 *
 * 帧上只有「这个节点停下来了」：要审什么、欠着谁，一律去执行详情读 pendingApprovals。
 */
export interface NodePausedEvent extends WorkflowRuntimeEventBase {
  type: 'node.paused';
  nodeId: string;
  nodeType?: NodeType;
  duration?: number;
}

/** 整条流暂停：本轮跑完但停在等人。同 node.paused，待办清单不在帧里 */
export interface WorkflowPausedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.paused';
  nodeId?: string;
  duration?: number;
  variables?: Record<string, unknown>;
}

export interface WorkflowCompletedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.completed';
  outputs?: Record<string, unknown>;
  duration?: number;
}

export interface WorkflowFailedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.failed';
  error: string;
}

export interface WorkflowCancelledEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.cancelled';
  reason?: string;
}

export type WorkflowRuntimeEvent =
  | NodeCancelledEvent
  | NodeCompletedEvent
  | NodeDeltaEvent
  | NodeFailedEvent
  | NodePausedEvent
  | NodeStartedEvent
  | NodeTimeoutEvent
  | NodeToolCallEvent
  | NodeToolResultEvent
  | WorkflowCancelledEvent
  | WorkflowCompletedEvent
  | WorkflowFailedEvent
  | WorkflowPausedEvent
  | WorkflowResumedEvent
  | WorkflowStartedEvent;

const runtimeEventTypeSet = new Set<string>(WORKFLOW_RUNTIME_EVENT_TYPES);

export function isWorkflowRuntimeEventType(
  type: unknown,
): type is WorkflowRuntimeEventType {
  return typeof type === 'string' && runtimeEventTypeSet.has(type);
}

export function parseRuntimeEvent(payload: unknown): WorkflowRuntimeEvent {
  if (!payload || typeof payload !== 'object') {
    throw new Error('工作流运行事件格式错误');
  }

  const type = (payload as { type?: unknown }).type;
  if (!isWorkflowRuntimeEventType(type)) {
    throw new Error(`未知工作流运行事件: ${String(type)}`);
  }

  return payload as WorkflowRuntimeEvent;
}
