<script setup lang="ts">
/**
 * 工作流诊断面板
 * 对标 Coze / Dify 的发布前质量检查，展示 工作流图的阻断、警告和优化建议。
 */
import type { WorkflowDiagnosticIssue } from '#/api/ai-workflow/types';

import { computed } from 'vue';

import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue';

const props = defineProps<{
  issues: WorkflowDiagnosticIssue[];
}>();

const errorIssues = computed(() =>
  props.issues.filter((issue) => issue.severity === 'ERROR'),
);
const warningIssues = computed(() =>
  props.issues.filter((issue) => issue.severity === 'WARNING'),
);
const suggestionIssues = computed(() =>
  props.issues.filter((issue) => issue.severity === 'SUGGESTION'),
);

function issueColor(issue: WorkflowDiagnosticIssue) {
  if (issue.severity === 'ERROR') return 'error';
  if (issue.severity === 'WARNING') return 'warning';
  return 'processing';
}

function issueIcon(issue: WorkflowDiagnosticIssue) {
  if (issue.severity === 'ERROR') return ExclamationCircleOutlined;
  if (issue.severity === 'WARNING') return WarningOutlined;
  return InfoCircleOutlined;
}
</script>

<template>
  <div class="workflow-diagnostics-panel">
    <div class="diagnostics-summary">
      <a-statistic title="阻断" :value="errorIssues.length" />
      <a-statistic title="警告" :value="warningIssues.length" />
      <a-statistic title="建议" :value="suggestionIssues.length" />
    </div>

    <a-empty v-if="issues.length === 0" description="当前工作流未发现诊断问题">
      <template #image>
        <CheckCircleOutlined class="empty-ok" />
      </template>
    </a-empty>

    <a-list
      v-else
      item-layout="vertical"
      :data-source="issues"
      class="diagnostics-list"
    >
      <template #renderItem="{ item }">
        <a-list-item class="diagnostic-item">
          <a-alert :type="issueColor(item)" show-icon>
            <template #icon>
              <component :is="issueIcon(item)" />
            </template>
            <template #message>
              <div class="issue-title">
                <span>{{ item.message }}</span>
                <a-tag>{{ item.code }}</a-tag>
              </div>
            </template>
            <template #description>
              <div class="issue-description">
                <div v-if="item.nodeLabel || item.nodeId" class="issue-node">
                  {{ item.nodeLabel || item.nodeId }}
                  <a-tag v-if="item.nodeType" color="blue">
                    {{ item.nodeType }}
                  </a-tag>
                </div>
                <div v-if="item.suggestion" class="issue-suggestion">
                  {{ item.suggestion }}
                </div>
              </div>
            </template>
          </a-alert>
        </a-list-item>
      </template>
    </a-list>
  </div>
</template>

<style scoped lang="less">
.workflow-diagnostics-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.diagnostics-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  padding: 12px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}

.diagnostics-list {
  :deep(.ant-list-item) {
    padding: 0 0 12px;
    border-block-end: none;
  }
}

.issue-title {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.issue-description {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.issue-node {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
}

.issue-suggestion {
  font-size: 12px;
  color: #595959;
}

.empty-ok {
  font-size: 48px;
  color: #52c41a;
}
</style>
