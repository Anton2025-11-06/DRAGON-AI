import type { WorkflowNodeCategory } from './node-definitions';

export interface WorkflowNodePanelCategory {
  key: WorkflowNodeCategory;
  label: string;
}

export const workflowNodePanelCategories: WorkflowNodePanelCategory[] = [
  { key: 'basic', label: '输入输出' },
  { key: 'ai', label: 'AI 能力' },
  { key: 'control', label: '控制流' },
  { key: 'data', label: '数据处理' },
  { key: 'external', label: '外部能力' },
];
