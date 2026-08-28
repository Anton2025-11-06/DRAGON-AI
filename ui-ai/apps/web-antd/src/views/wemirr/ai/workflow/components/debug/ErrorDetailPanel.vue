<script setup lang="ts">
/**
 * 错误详情面板组件 (Coze-Style)
 * 显示错误类型、消息、位置，提供修复建议，支持一键跳转和复制
 *
 */

import type { WorkflowDebugErrorType as WorkflowDebugErrorTypeValue } from '../../domain/debug-errors';

import { computed, h, ref } from 'vue';

import {
  BugOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  InfoCircleOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue';
import {
  Alert,
  Button,
  Card,
  Collapse,
  CollapsePanel,
  Divider,
  Empty,
  message,
  Space,
  Tag,
  Timeline,
  TimelineItem,
  Tooltip,
  Typography,
} from 'ant-design-vue';

import { WorkflowDebugErrorType } from '../../domain/debug-errors';

const props = withDefaults(defineProps<Props>(), {
  error: null,
  errorHistory: () => [],
  showHistory: true,
});

// ==================== Emits ====================

const emit = defineEmits<{
  /** 跳转到节点 */
  (e: 'navigate-to-node', nodeId: string): void;
  /** 重试操作 */
  (e: 'retry'): void;
  /** 清除错误 */
  (e: 'clear'): void;
  /** 查看历史错误详情 */
  (e: 'view-history-error', error: ErrorHistoryItem): void;
  /** 清除历史记录 */
  (e: 'clear-history'): void;
}>();

const { Text, Paragraph } = Typography;

// ==================== Types ====================

export type ErrorType = WorkflowDebugErrorTypeValue;

/**
 * 错误详情接口
 */
export interface ErrorDetail {
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
}

/**
 * 错误历史记录
 */
export interface ErrorHistoryItem extends ErrorDetail {
  /** 是否已解决 */
  resolved: boolean;
}

// ==================== Props ====================

interface Props {
  /** 当前错误详情 */
  error?: ErrorDetail | null;
  /** 错误历史记录 */
  errorHistory?: ErrorHistoryItem[];
  /** 是否显示历史记录 */
  showHistory?: boolean;
}

// ==================== State ====================

/** 展开的面板 */
const activeKey = ref<string[]>(['error-detail']);

// ==================== Computed ====================

/** 是否有错误 */
const hasError = computed(() => props.error !== null);

/** 是否有历史记录 */
const hasHistory = computed(() => props.errorHistory.length > 0);

/** 错误类型配置 */
const errorTypeConfig = computed(() => {
  if (!props.error) return null;
  return getErrorTypeConfig(props.error.type);
});

// ==================== Methods ====================

/**
 * 获取错误类型配置
 */
function getErrorTypeConfig(type: ErrorType) {
  const configs: Record<
    ErrorType,
    { color: string; description: string; icon: any; label: string }
  > = {
    [WorkflowDebugErrorType.NETWORK]: {
      label: '网络错误',
      color: 'error',
      icon: CloseCircleOutlined,
      description: '网络连接失败或请求超时',
    },
    [WorkflowDebugErrorType.TIMEOUT]: {
      label: '超时错误',
      color: 'warning',
      icon: WarningOutlined,
      description: '操作执行超时',
    },
    [WorkflowDebugErrorType.VALIDATION]: {
      label: '验证错误',
      color: 'orange',
      icon: ExclamationCircleOutlined,
      description: '输入数据验证失败',
    },
    [WorkflowDebugErrorType.EXECUTION]: {
      label: '执行错误',
      color: 'error',
      icon: BugOutlined,
      description: '节点执行过程中发生错误',
    },
    [WorkflowDebugErrorType.CONFIGURATION]: {
      label: '配置错误',
      color: 'purple',
      icon: InfoCircleOutlined,
      description: '节点配置不正确',
    },
    [WorkflowDebugErrorType.PERMISSION]: {
      label: '权限错误',
      color: 'red',
      icon: CloseCircleOutlined,
      description: '没有执行此操作的权限',
    },
    [WorkflowDebugErrorType.RESOURCE]: {
      label: '资源错误',
      color: 'volcano',
      icon: WarningOutlined,
      description: '所需资源不可用',
    },
    [WorkflowDebugErrorType.UNKNOWN]: {
      label: '未知错误',
      color: 'default',
      icon: QuestionCircleOutlined,
      description: '发生了未知错误',
    },
  };
  return configs[type] || configs[WorkflowDebugErrorType.UNKNOWN];
}

/**
 * 格式化时间
 */
function formatTime(date: Date): string {
  return new Date(date).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * 复制错误信息到剪贴板
 */
async function copyErrorInfo() {
  if (!props.error) return;

  const errorInfo = {
    type: props.error.type,
    message: props.error.message,
    nodeId: props.error.nodeId,
    nodeName: props.error.nodeName,
    timestamp: props.error.timestamp,
    stackTrace: props.error.stackTrace,
    context: props.error.context,
  };

  try {
    await navigator.clipboard.writeText(JSON.stringify(errorInfo, null, 2));
    message.success('错误信息已复制到剪贴板');
  } catch {
    message.error('复制失败');
  }
}

/**
 * 复制堆栈跟踪
 */
async function copyStackTrace() {
  if (!props.error?.stackTrace) return;

  try {
    await navigator.clipboard.writeText(props.error.stackTrace);
    message.success('堆栈跟踪已复制到剪贴板');
  } catch {
    message.error('复制失败');
  }
}

/**
 * 跳转到出错节点
 */
function navigateToNode() {
  if (props.error?.nodeId) {
    emit('navigate-to-node', props.error.nodeId);
  }
}

/**
 * 重试操作
 */
function handleRetry() {
  emit('retry');
}

/**
 * 查看历史错误
 */
function viewHistoryError(error: ErrorHistoryItem) {
  emit('view-history-error', error);
}

/**
 * 清除历史记录
 */
function clearHistory() {
  emit('clear-history');
}

/**
 * 获取默认修复建议
 */
function getDefaultSuggestions(type: ErrorType): string[] {
  const suggestions: Record<ErrorType, string[]> = {
    [WorkflowDebugErrorType.NETWORK]: [
      '检查网络连接是否正常',
      '确认服务器地址是否正确',
      '尝试刷新页面后重试',
    ],
    [WorkflowDebugErrorType.TIMEOUT]: [
      '检查网络连接速度',
      '尝试减少处理的数据量',
      '联系管理员检查服务器负载',
    ],
    [WorkflowDebugErrorType.VALIDATION]: [
      '检查输入数据格式是否正确',
      '确认必填字段是否已填写',
      '查看字段的约束条件',
    ],
    [WorkflowDebugErrorType.EXECUTION]: [
      '检查节点配置是否正确',
      '查看输入数据是否符合预期',
      '检查依赖的外部服务是否可用',
    ],
    [WorkflowDebugErrorType.CONFIGURATION]: [
      '检查节点配置参数',
      '确认所有必填配置项已设置',
      '参考文档确认配置格式',
    ],
    [WorkflowDebugErrorType.PERMISSION]: [
      '确认当前用户是否有相应权限',
      '联系管理员获取权限',
      '检查 API 密钥是否有效',
    ],
    [WorkflowDebugErrorType.RESOURCE]: [
      '检查所需资源是否存在',
      '确认资源访问路径是否正确',
      '检查资源配额是否充足',
    ],
    [WorkflowDebugErrorType.UNKNOWN]: [
      '尝试刷新页面后重试',
      '检查浏览器控制台是否有更多信息',
      '联系技术支持获取帮助',
    ],
  };
  return suggestions[type] || suggestions[WorkflowDebugErrorType.UNKNOWN];
}

/**
 * 获取修复建议列表
 */
const suggestions = computed(() => {
  if (!props.error) return [];
  return props.error.suggestions?.length
    ? props.error.suggestions
    : getDefaultSuggestions(props.error.type);
});
</script>

<template>
  <div class="error-detail-panel">
    <!-- 空状态 -->
    <Empty
      v-if="!hasError && !hasHistory"
      description="暂无错误信息"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />

    <!-- 当前错误详情 -->
    <template v-if="hasError">
      <Card class="error-card" :bordered="false">
        <!-- 错误头部 -->
        <div class="error-header">
          <div class="error-type">
            <component
              :is="errorTypeConfig?.icon"
              class="error-icon"
              :style="{ color: `var(--ant-color-${errorTypeConfig?.color})` }"
            />
            <Tag :color="errorTypeConfig?.color" size="large">
              {{ errorTypeConfig?.label }}
            </Tag>
          </div>
          <div class="error-actions">
            <Space>
              <Tooltip title="复制错误信息">
                <Button type="text" size="small" @click="copyErrorInfo">
                  <template #icon><CopyOutlined /></template>
                </Button>
              </Tooltip>
              <Tooltip v-if="error?.nodeId" title="跳转到出错节点">
                <Button type="primary" size="small" @click="navigateToNode">
                  <template #icon><EyeOutlined /></template>
                  定位节点
                </Button>
              </Tooltip>
              <Tooltip v-if="error?.retryable" title="重试">
                <Button type="default" size="small" @click="handleRetry">
                  <template #icon><ReloadOutlined /></template>
                  重试
                </Button>
              </Tooltip>
            </Space>
          </div>
        </div>

        <Divider style="margin: 12px 0" />

        <!-- 错误消息 -->
        <div class="error-message">
          <Text strong>错误消息</Text>
          <Paragraph class="message-text" :copyable="{ text: error?.message }">
            {{ error?.message }}
          </Paragraph>
        </div>

        <!-- 错误位置 -->
        <div v-if="error?.nodeId" class="error-location">
          <Text strong>发生位置</Text>
          <div class="location-info">
            <Tag color="blue">{{ error?.nodeName || error?.nodeId }}</Tag>
            <Text type="secondary" class="node-id">
              ID: {{ error?.nodeId }}
            </Text>
          </div>
        </div>

        <!-- 发生时间 -->
        <div class="error-time">
          <Text strong>发生时间</Text>
          <Text type="secondary">{{ formatTime(error!.timestamp) }}</Text>
        </div>

        <!-- 修复建议 -->
        <div v-if="suggestions.length > 0" class="error-suggestions">
          <Text strong>修复建议</Text>
          <Alert type="info" show-icon :icon="h(InfoCircleOutlined)">
            <template #message>
              <ul class="suggestion-list">
                <li v-for="(suggestion, index) in suggestions" :key="index">
                  {{ suggestion }}
                </li>
              </ul>
            </template>
          </Alert>
        </div>

        <!-- 堆栈跟踪 -->
        <Collapse
          v-if="error?.stackTrace"
          v-model:active-key="activeKey"
          class="stack-collapse"
        >
          <CollapsePanel key="stack-trace" header="堆栈跟踪">
            <div class="stack-trace-container">
              <pre class="stack-trace">{{ error?.stackTrace }}</pre>
              <Tooltip title="复制堆栈跟踪">
                <Button
                  type="link"
                  size="small"
                  class="copy-stack-btn"
                  @click="copyStackTrace"
                >
                  <CopyOutlined /> 复制
                </Button>
              </Tooltip>
            </div>
          </CollapsePanel>
        </Collapse>

        <!-- 上下文信息 -->
        <Collapse v-if="error?.context" class="context-collapse">
          <CollapsePanel key="context" header="上下文信息">
            <pre class="context-json">{{
              JSON.stringify(error?.context, null, 2)
            }}</pre>
          </CollapsePanel>
        </Collapse>
      </Card>
    </template>

    <!-- 错误历史记录 -->
    <template v-if="showHistory && hasHistory">
      <Divider v-if="hasError" />
      <div class="error-history">
        <div class="history-header">
          <Text strong>错误历史</Text>
          <Button type="link" size="small" danger @click="clearHistory">
            清除历史
          </Button>
        </div>
        <Timeline class="history-timeline">
          <TimelineItem
            v-for="historyError in errorHistory"
            :key="historyError.id"
            :color="historyError.resolved ? 'green' : 'red'"
          >
            <div class="history-item" @click="viewHistoryError(historyError)">
              <div class="history-item-header">
                <Tag
                  :color="getErrorTypeConfig(historyError.type).color"
                  size="small"
                >
                  {{ getErrorTypeConfig(historyError.type).label }}
                </Tag>
                <Text type="secondary" class="history-time">
                  {{ formatTime(historyError.timestamp) }}
                </Text>
              </div>
              <Text class="history-message" ellipsis>
                {{ historyError.message }}
              </Text>
              <Text
                v-if="historyError.nodeName"
                type="secondary"
                class="history-node"
              >
                节点: {{ historyError.nodeName }}
              </Text>
            </div>
          </TimelineItem>
        </Timeline>
      </div>
    </template>
  </div>
</template>

<style lang="less" scoped>
.error-detail-panel {
  height: 100%;
  overflow-y: auto;
  padding: 12px;
  background-color: var(--ant-color-bg-container);
}

.error-card {
  background-color: var(--ant-color-error-bg);
  border: 1px solid var(--ant-color-error-border);
  border-radius: 8px;

  .error-header {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .error-type {
      display: flex;
      align-items: center;
      gap: 8px;

      .error-icon {
        font-size: 24px;
      }
    }
  }

  .error-message {
    margin-bottom: 16px;

    .message-text {
      margin-top: 8px;
      padding: 12px;
      background-color: var(--ant-color-bg-container);
      border-radius: 6px;
      font-family: 'Fira Code', monospace;
      font-size: 13px;
      word-break: break-word;
    }
  }

  .error-location {
    margin-bottom: 16px;

    .location-info {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;

      .node-id {
        font-size: 12px;
      }
    }
  }

  .error-time {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
  }

  .error-suggestions {
    margin-bottom: 16px;

    .suggestion-list {
      margin: 0;
      padding-left: 20px;

      li {
        margin-bottom: 4px;
        line-height: 1.6;

        &:last-child {
          margin-bottom: 0;
        }
      }
    }
  }

  .stack-collapse,
  .context-collapse {
    margin-top: 12px;
    background-color: transparent;

    :deep(.ant-collapse-header) {
      padding: 8px 12px;
      background-color: var(--ant-color-bg-container);
      border-radius: 6px;
    }

    :deep(.ant-collapse-content-box) {
      padding: 0;
    }
  }

  .stack-trace-container {
    position: relative;

    .stack-trace {
      max-height: 300px;
      padding: 12px;
      margin: 0;
      overflow: auto;
      font-family: 'Fira Code', monospace;
      font-size: 12px;
      line-height: 1.5;
      background-color: var(--ant-color-bg-layout);
      border-radius: 6px;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .copy-stack-btn {
      position: absolute;
      top: 8px;
      right: 8px;
    }
  }

  .context-json {
    max-height: 200px;
    padding: 12px;
    margin: 0;
    overflow: auto;
    font-family: 'Fira Code', monospace;
    font-size: 12px;
    background-color: var(--ant-color-bg-layout);
    border-radius: 6px;
  }
}

.error-history {
  margin-top: 16px;

  .history-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .history-timeline {
    padding: 0 8px;

    .history-item {
      cursor: pointer;
      padding: 8px;
      margin: -8px;
      border-radius: 6px;
      transition: background-color 0.2s;

      &:hover {
        background-color: var(--ant-color-bg-layout);
      }

      .history-item-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;

        .history-time {
          font-size: 12px;
        }
      }

      .history-message {
        display: block;
        font-size: 13px;
        max-width: 300px;
      }

      .history-node {
        display: block;
        font-size: 12px;
        margin-top: 4px;
      }
    }
  }
}

// 暗色模式适配
html[class='dark'] {
  .error-card {
    background-color: rgba(255, 77, 79, 0.1);
    border-color: rgba(255, 77, 79, 0.3);

    .error-message .message-text {
      background-color: var(--ant-color-bg-elevated);
    }

    .stack-trace,
    .context-json {
      background-color: var(--ant-color-bg-elevated);
    }
  }

  .history-item:hover {
    background-color: var(--ant-color-bg-elevated);
  }
}
</style>
