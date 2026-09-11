import type { NodeType } from '#/api/ai-workflow/types';

export const ALL_NODE_TYPES = [
  'START',
  'END',
  'VARIABLE_ASSIGNER',
  'LLM',
  'KNOWLEDGE_RETRIEVAL',
  'QUESTION_CLASSIFIER',
  'PARAMETER_EXTRACTOR',
  'AGENT',
  'IF_ELSE',
  'ITERATION',
  'VARIABLE_AGGREGATOR',
  'LOOP',
  'PARALLEL',
  'CODE',
  'TEMPLATE',
  'REPLY',
  'DOC_EXTRACTOR',
  'LIST_OPERATOR',
  'HTTP_REQUEST',
  'TOOL',
] as const satisfies readonly NodeType[];

export type WorkflowNodeCategory =
  | 'ai'
  | 'basic'
  | 'control'
  | 'data'
  | 'external';

export interface WorkflowPortDefinition {
  id: string;
  name: string;
  direction: 'input' | 'output';
  multiple?: boolean;
}

export interface WorkflowNodeDefinition {
  type: NodeType;
  displayName: string;
  description: string;
  category: WorkflowNodeCategory;
  icon: string;
  color: string;
  inputs: WorkflowPortDefinition[];
  outputs: WorkflowPortDefinition[];
  formComponent: string;
  defaultConfig: Record<string, unknown>;
}
