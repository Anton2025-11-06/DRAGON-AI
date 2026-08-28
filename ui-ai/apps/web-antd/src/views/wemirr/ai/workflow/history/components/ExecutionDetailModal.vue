<script lang="ts" setup>
/**
 * 执行详情弹窗组件
 * 展示工作流执行的基本信息和节点执行时间线
 */
import type { WorkflowExecutionResp } from '#/api/ai-workflow/types';

import { computed, ref } from 'vue';

import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  StopOutlined,
} from '@ant-design/icons-vue';
import { Collapse, Descriptions, Tag, Timeline } from 'ant-design-vue';

interface Props {
  open: boolean;
  execution?: WorkflowExecutionResp;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
}>();

const loading = ref(false);

// 状态配置
const statusConfig: Record<string, { color: string; text: string; icon: any }> =
  {
    PENDING: { color: 'default', text: '等待中', icon: ClockCircleOutlined },
    RUNNING: { color: 'processing', text: '执行中', icon: LoadingOutlined },
    COMPLETED: { color: 'success', text: '已完成', icon: CheckCircleOutlined },
    FAILED: { color: 'error', text: '失败', icon: CloseCircleOutlined },
    PAUSED: { color: 'warning', text: '已暂停', icon: PauseCircleOutlined },
    CANCELLED: { color: 'default', text: '已取消', icon: StopOutlined },
  };

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
});

function formatDuration(ms?: number): string {
  if (!ms) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

function formatTime(time?: string): string {
  if (!time) return '-';
  return new Date(time).toLocaleString('zh-CN');
}

function formatJson(data: any): string {
  if (data === null || data === undefined) return '-';
  if (typeof data === 'object' && Object.keys(data).length === 0) return '(空)';
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

/** 获取节点执行列表（按执行顺序排序） */
const nodeExecutionList = computed(() => {
  const nodeStates = props.execution?.nodeStates;
  if (!nodeStates) return [];

  return Object.entries(nodeStates)
    .map(([nodeId, state]) => ({
      nodeId,
      ...(state as any),
    }))
    .sort((a, b) => (a.order || 0) - (b.order || 0));
});

/** 获取节点状态颜色 */
function getNodeStatusColor(status?: string): string {
  const colors: Record<string, string> = {
    COMPLETED: 'green',
    FAILED: 'red',
    RUNNING: 'blue',
    PENDING: 'gray',
  };
  return colors[status || 'PENDING'] || 'gray';
}

/** 获取节点状态文本 */
function getNodeStatusText(status?: string): string {
  const texts: Record<string, string> = {
    COMPLETED: '已完成',
    FAILED: '失败',
    RUNNING: '执行中',
    PENDING: '等待中',
  };
  return texts[status || 'PENDING'] || status || '-';
}
</script>

<template>
  <a-modal
    v-model:open="visible"
    title="执行详情"
    width="800px"
    :footer="null"
    :body-style="{ maxHeight: '70vh', overflow: 'auto' }"
  >
    <a-spin :spinning="loading">
      <div v-if="execution" class="execution-detail">
        <!-- 基本信息 -->
        <Descriptions :column="2" bordered size="small">
          <Descriptions.Item label="执行ID" :span="2">
            {{ execution.executionId }}
          </Descriptions.Item>
          <Descriptions.Item label="工作流">
            {{ execution.workflowName || '-' }}
          </Descriptions.Item>
          <Descriptions.Item label="版本">
            v{{ execution.workflowVersion }}
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag :color="statusConfig[execution.status]?.color">
              <component
                :is="statusConfig[execution.status]?.icon"
                style="margin-right: 4px"
              />
              {{ statusConfig[execution.status]?.text }}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="耗时">
            {{ formatDuration(execution.duration) }}
          </Descriptions.Item>
          <Descriptions.Item label="开始时间">
            {{ formatTime(execution.startTime) }}
          </Descriptions.Item>
          <Descriptions.Item label="结束时间">
            {{ formatTime(execution.endTime) }}
          </Descriptions.Item>
          <Descriptions.Item
            v-if="execution.totalTokens"
            label="Token消耗"
            :span="2"
          >
            <a-space :size="16">
              <span
                >总计:
                <strong>{{
                  execution.totalTokens?.toLocaleString()
                }}</strong></span
              >
              <span
                >输入: {{ execution.inputTokens?.toLocaleString() || 0 }}</span
              >
              <span
                >输出: {{ execution.outputTokens?.toLocaleString() || 0 }}</span
              >
              <span v-if="execution.llmCallCount"
                >LLM调用: {{ execution.llmCallCount }}次</span
              >
            </a-space>
          </Descriptions.Item>
        </Descriptions>

        <!-- 错误信息 -->
        <div v-if="execution.errorMessage" class="error-section">
          <h4>错误信息</h4>
          <a-alert type="error" :message="execution.errorMessage" show-icon />
        </div>

        <!-- 输入参数 -->
        <div class="data-section">
          <h4>输入参数</h4>
          <pre class="json-view">{{ formatJson(execution.inputs) }}</pre>
        </div>

        <!-- 输出结果 -->
        <div class="data-section">
          <h4>输出结果</h4>
          <Collapse v-if="execution.outputs" :default-active-key="['output']">
            <Collapse.Panel key="output" header="查看输出">
              <pre class="json-view-small">{{
                formatJson(execution.outputs)
              }}</pre>
            </Collapse.Panel>
          </Collapse>
          <div v-else class="empty-tip">无输出</div>
        </div>

        <!-- 节点执行时间线 -->
        <div v-if="nodeExecutionList.length" class="timeline-section">
          <h4>节点执行详情</h4>
          <Timeline>
            <Timeline.Item
              v-for="node in nodeExecutionList"
              :key="node.nodeId"
              :color="getNodeStatusColor(node.status)"
            >
              <div class="timeline-node">
                <div class="timeline-header">
                  <span class="node-id">{{ node.nodeId }}</span>
                  <Tag :color="getNodeStatusColor(node.status)" size="small">
                    {{ getNodeStatusText(node.status) }}
                  </Tag>
                  <span v-if="node.duration" class="node-duration">
                    {{ formatDuration(node.duration) }}
                  </span>
                </div>
                <div v-if="node.error" class="node-error">
                  <a-alert type="error" :message="node.error" size="small" />
                </div>
                <Collapse
                  v-if="node.input || node.output"
                  size="small"
                  class="node-detail-collapse"
                >
                  <Collapse.Panel v-if="node.input" key="input" header="输入">
                    <pre class="json-view-small">{{
                      formatJson(node.input)
                    }}</pre>
                  </Collapse.Panel>
                  <Collapse.Panel v-if="node.output" key="output" header="输出">
                    <pre class="json-view-small">{{
                      formatJson(node.output)
                    }}</pre>
                  </Collapse.Panel>
                </Collapse>
              </div>
            </Timeline.Item>
          </Timeline>
        </div>
      </div>

      <div v-else class="empty-state">
        <a-empty description="暂无数据" />
      </div>
    </a-spin>
  </a-modal>
</template>

<style lang="less" scoped>
.execution-detail {
  .error-section,
  .data-section,
  .nodes-section,
  .timeline-section {
    margin-top: 16px;

    h4 {
      margin-bottom: 8px;
      font-size: 14px;
      font-weight: 500;
      color: #333;
    }
  }

  .json-view {
    max-height: 200px;
    padding: 12px;
    margin: 0;
    overflow: auto;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 12px;
    line-height: 1.5;
    background-color: #f5f5f5;
    border-radius: 4px;
  }

  .json-view-small {
    max-height: 300px;
    padding: 8px;
    margin: 0;
    overflow: auto;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    background-color: #fafafa;
    border-radius: 4px;
  }

  .text-view {
    max-height: 400px;
    padding: 12px;
    overflow: auto;
    font-size: 14px;
    line-height: 1.8;
    white-space: pre-wrap;
    word-break: break-word;
    background-color: #fafafa;
    border-radius: 4px;
  }

  .empty-tip {
    padding: 12px;
    color: #999;
    text-align: center;
    background-color: #fafafa;
    border-radius: 4px;
  }

  .timeline-section {
    .timeline-node {
      .timeline-header {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        margin-bottom: 8px;

        .node-id {
          font-weight: 500;
          color: #333;
        }

        .node-duration {
          font-size: 12px;
          color: #999;
        }
      }

      .node-error {
        margin-bottom: 8px;
      }

      .node-detail-collapse {
        margin-top: 4px;
      }
    }
  }
}

.empty-state {
  padding: 40px 0;
}
</style>
