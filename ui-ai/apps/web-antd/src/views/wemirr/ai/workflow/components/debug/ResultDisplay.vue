<script setup lang="ts">
/**
 * 执行结果展示组件 (Coze-Style)
 * 显示工作流执行结果，支持 Markdown、JSON、图片等内容类型
 *
 */

import type { EndNodeOutput, ExecutionResult } from '#/store/debug-store';

import { computed } from 'vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';

import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  FileTextOutlined,
  PictureOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import {
  Card,
  Empty,
  Image,
  message,
  Statistic,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import MarkdownRenderer from './MarkdownRenderer.vue';

// ==================== Props ====================

interface Props {
  /** 执行结果数据 */
  result: ExecutionResult | null;
}

const props = defineProps<Props>();

// ==================== 计算属性 ====================

/** 是否有结果 */
const hasResult = computed(() => props.result !== null);

/** 是否执行成功 */
const isSuccess = computed(() => props.result?.status === 'completed');

/** 是否有 END 节点输出 */
const hasEndNodeOutputs = computed(
  () => props.result?.endNodeOutputs && props.result.endNodeOutputs.length > 0,
);

/** 状态标签颜色 */
const statusColor = computed(() => (isSuccess.value ? 'success' : 'error'));

/** 状态文本 */
const statusText = computed(() => (isSuccess.value ? '执行成功' : '执行失败'));

/** 状态图标 */
const statusIcon = computed(() =>
  isSuccess.value ? CheckCircleOutlined : CloseCircleOutlined,
);

// ==================== 工具函数 ====================

/**
 * 格式化执行时间
 */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = ((ms % 60_000) / 1000).toFixed(1);
  return `${minutes}m ${seconds}s`;
}

/**
 * 获取内容类型图标
 */
function getContentTypeIcon(contentType: string) {
  const iconMap: Record<string, any> = {
    text: FileTextOutlined,
    markdown: FileTextOutlined,
    json: ThunderboltOutlined,
    image: PictureOutlined,
  };
  return iconMap[contentType] || FileTextOutlined;
}

/**
 * 获取内容类型标签
 */
function getContentTypeLabel(contentType: string): string {
  const labelMap: Record<string, string> = {
    text: '文本',
    markdown: 'Markdown',
    json: 'JSON',
    image: '图片',
  };
  return labelMap[contentType] || contentType;
}

/**
 * 解析 JSON 内容
 */
function parseJsonContent(content: string): any {
  try {
    return JSON.parse(content);
  } catch {
    return content;
  }
}

/**
 * 复制内容到剪贴板
 */
async function copyToClipboard(content: string) {
  try {
    await navigator.clipboard.writeText(content);
    message.success('已复制到剪贴板');
  } catch {
    message.error('复制失败');
  }
}

/**
 * 复制输出内容
 */
function copyOutput(output: EndNodeOutput) {
  copyToClipboard(output.content);
}
</script>

<template>
  <div class="result-display">
    <!-- 空状态 -->
    <Empty
      v-if="!hasResult"
      description="暂无执行结果"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />

    <!-- 执行结果内容 -->
    <template v-else>
      <!-- 执行摘要 -->
      <div class="result-summary">
        <div class="summary-header">
          <Tag :color="statusColor" size="large" class="status-tag">
            <template #icon>
              <component :is="statusIcon" />
            </template>
            {{ statusText }}
          </Tag>
        </div>

        <div class="summary-stats">
          <div class="stat-item">
            <Statistic title="总耗时" :value-style="{ fontSize: '18px' }">
              <template #formatter>
                <span class="stat-value">
                  <ClockCircleOutlined class="stat-icon" />
                  {{ formatDuration(result!.totalDuration) }}
                </span>
              </template>
            </Statistic>
          </div>

          <div v-if="result!.totalTokens" class="stat-item">
            <Statistic title="Token 消耗" :value-style="{ fontSize: '18px' }">
              <template #formatter>
                <span class="stat-value">
                  <ThunderboltOutlined class="stat-icon" />
                  {{ result!.totalTokens.toLocaleString() }}
                </span>
              </template>
            </Statistic>
          </div>
        </div>
      </div>

      <!-- 输出内容卡片 -->
      <div v-if="hasEndNodeOutputs" class="output-cards">
        <div class="cards-title">输出结果</div>

        <Card
          v-for="output in result!.endNodeOutputs"
          :key="output.nodeId"
          class="output-card"
          :body-style="{ padding: '16px' }"
        >
          <template #title>
            <div class="card-title">
              <component
                :is="getContentTypeIcon(output.contentType)"
                class="title-icon"
              />
              <span>{{ output.nodeName }}</span>
              <Tag size="small" class="type-tag">
                {{ getContentTypeLabel(output.contentType) }}
              </Tag>
            </div>
          </template>

          <template #extra>
            <Tooltip title="复制内容">
              <a class="copy-btn" @click="copyOutput(output)">
                <CopyOutlined />
              </a>
            </Tooltip>
          </template>

          <!-- 文本内容 -->
          <div
            v-if="output.contentType === 'text'"
            class="output-content output-text"
          >
            <pre>{{ output.content }}</pre>
          </div>

          <!-- Markdown 内容 -->
          <div
            v-else-if="output.contentType === 'markdown'"
            class="output-content output-markdown"
          >
            <MarkdownRenderer :content="output.content" />
          </div>

          <!-- JSON 内容 -->
          <div
            v-else-if="output.contentType === 'json'"
            class="output-content output-json"
          >
            <VueJsonPretty
              :data="parseJsonContent(output.content)"
              :deep="3"
              :show-length="true"
            />
          </div>

          <!-- 图片内容 -->
          <div
            v-else-if="output.contentType === 'image'"
            class="output-content output-image"
          >
            <Image :src="output.content" :preview="true" />
          </div>
        </Card>
      </div>

      <!-- 无输出内容提示 -->
      <div v-else class="no-outputs">
        <Empty description="无输出内容" :image="Empty.PRESENTED_IMAGE_SIMPLE" />
      </div>
    </template>
  </div>
</template>

<style lang="less" scoped>
.result-display {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
  background-color: var(--ant-color-bg-container);

  .result-summary {
    padding: 16px;
    margin-bottom: 16px;
    background-color: var(--ant-color-bg-layout);
    border-radius: 8px;

    .summary-header {
      margin-bottom: 16px;

      .status-tag {
        font-size: 14px;
        padding: 4px 12px;
      }
    }

    .summary-stats {
      display: flex;
      gap: 32px;

      .stat-item {
        .stat-value {
          display: flex;
          align-items: center;
          gap: 6px;

          .stat-icon {
            font-size: 16px;
            color: var(--ant-color-primary);
          }
        }
      }
    }
  }

  .output-cards {
    .cards-title {
      margin-bottom: 12px;
      font-size: 14px;
      font-weight: 500;
      color: var(--ant-color-text-secondary);
    }

    .output-card {
      margin-bottom: 12px;

      .card-title {
        display: flex;
        align-items: center;
        gap: 8px;

        .title-icon {
          font-size: 16px;
          color: var(--ant-color-primary);
        }

        .type-tag {
          margin-left: auto;
        }
      }

      .copy-btn {
        color: var(--ant-color-text-secondary);
        cursor: pointer;
        transition: color 0.2s;

        &:hover {
          color: var(--ant-color-primary);
        }
      }

      .output-content {
        max-height: 400px;
        overflow: auto;
      }

      .output-text {
        pre {
          margin: 0;
          padding: 12px;
          font-family: 'Fira Code', monospace;
          font-size: 13px;
          white-space: pre-wrap;
          word-break: break-word;
          background-color: var(--ant-color-bg-layout);
          border-radius: 4px;
        }
      }

      .output-markdown {
        padding: 12px;
        background-color: var(--ant-color-bg-layout);
        border-radius: 4px;
      }

      .output-json {
        padding: 12px;
        background-color: var(--ant-color-bg-layout);
        border-radius: 4px;

        :deep(.vjs-tree) {
          font-size: 13px;
        }
      }

      .output-image {
        text-align: center;

        :deep(.ant-image) {
          max-width: 100%;

          img {
            max-height: 300px;
            border-radius: 4px;
          }
        }
      }
    }
  }

  .no-outputs {
    padding: 32px;
    text-align: center;
  }
}

// 暗色模式适配
html[class='dark'] {
  .result-display {
    .result-summary {
      background-color: var(--ant-color-bg-elevated);
    }

    .output-card {
      .output-text pre,
      .output-markdown,
      .output-json {
        background-color: #1e1e1e;
      }
    }
  }
}
</style>
