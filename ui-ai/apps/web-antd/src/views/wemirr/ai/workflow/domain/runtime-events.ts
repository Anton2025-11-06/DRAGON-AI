import type { ExecutionEventType, NodeType } from '#/api/ai-workflow/types';

import { WORKFLOW_RUNTIME_EVENT_TYPES } from '#/api/ai-workflow/types';

export { WORKFLOW_RUNTIME_EVENT_TYPES };

export type WorkflowRuntimeEventType = ExecutionEventType;

export interface WorkflowRuntimeEventBase {
  type: WorkflowRuntimeEventType;
  executionId?: string;
  timestamp?: number;
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
}

export interface NodeCompletedEvent extends WorkflowRuntimeEventBase {
  type: 'node.completed';
  nodeId: string;
  output?: unknown;
  duration?: number;
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

export interface WorkflowPausedEvent extends WorkflowRuntimeEventBase {
  type: 'workflow.paused';
  nodeId: string;
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
  | NodeCompletedEvent
  | NodeDeltaEvent
  | NodeFailedEvent
  | NodeStartedEvent
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
