/**
 * AI 工作流模块导出
 */

export * from './components';

export { default as WorkflowEditorPage } from './editor/index.vue';
export { default as WorkflowHistoryPage } from './history/index.vue';
// 页面组件
export { default as WorkflowListPage } from './list/index.vue';
export { default as SaveAsTemplateModal } from './templates/components/SaveAsTemplateModal.vue';

export { default as TemplateCard } from './templates/components/TemplateCard.vue';
// 模板组件
export { default as TemplateSelectModal } from './templates/components/TemplateSelectModal.vue';
export { default as WorkflowTemplatesPage } from './templates/index.vue';
