/**
 * 节点配置表单组件导出
 * 同步自后端 NodeType 枚举，支持所有 工作流节点
 */

export { default as AgentNodeForm } from './AgentNodeForm.vue';
// 能力节点
export { default as CodeNodeForm } from './CodeNodeForm.vue';
export { default as DocExtractorNodeForm } from './DocExtractorNodeForm.vue';

export { default as EndNodeForm } from './EndNodeForm.vue';
// 外部系统节点
export { default as HttpNodeForm } from './HttpNodeForm.vue';
// 控制流节点
export { default as IfElseNodeForm } from './IfElseNodeForm.vue';
export { default as IterationNodeForm } from './IterationNodeForm.vue';
export { default as KnowledgeNodeForm } from './KnowledgeNodeForm.vue';

export { default as ListOperatorNodeForm } from './ListOperatorNodeForm.vue';
// 智能体节点
export { default as LLMNodeForm } from './LLMNodeForm.vue';
export { default as LoopNodeForm } from './LoopNodeForm.vue';
export { default as ParallelNodeForm } from './ParallelNodeForm.vue';
export { default as ParameterExtractorNodeForm } from './ParameterExtractorNodeForm.vue';

export { default as QuestionClassifierNodeForm } from './QuestionClassifierNodeForm.vue';
// 工作流边界
export { default as StartNodeForm } from './StartNodeForm.vue';
export { default as TemplateNodeForm } from './TemplateNodeForm.vue';
export { default as ToolNodeForm } from './ToolNodeForm.vue';

export { default as VariableAggregatorNodeForm } from './VariableAggregatorNodeForm.vue';
export { default as VariableNodeForm } from './VariableNodeForm.vue';
