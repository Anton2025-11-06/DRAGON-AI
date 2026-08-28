<script setup lang="ts">
/**
 * 节点追踪面板组件 (Coze-Style)
 * 显示节点执行的详细信息，包括输入输出、耗时、Token 消耗等
 * 优化数据展示格式，提供更好的用户体验
 *
 */

import type { NodeTrace } from '#/store/debug-store';

import { computed, ref } from 'vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';

import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  FullscreenOutlined,
  FunctionOutlined,
  LoadingOutlined,
  MessageOutlined,
  MinusCircleOutlined,
  MinusOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import {
  Alert,
  Collapse,
  CollapsePanel,
  Descriptions,
  DescriptionsItem,
  Empty,
  Input,
  message,
  Modal,
  Statistic,
  Tag,
  Tooltip,
} from 'ant-design-vue';

// ==================== Props ====================

interface Props {
  /** 节点追踪数据 */
  trace: NodeTrace | null;
}

const props = defineProps<Props>();

// ==================== 计算属性 ====================

/** 是否有数据 */
const hasTrace = computed(() => props.trace !== null);

/** 是否有输入数据 */
const hasInputs = computed(() => {
  return props.trace?.inputs && Object.keys(props.trace.inputs).length > 0;
});

/** 是否有输出数据 */
const hasOutputs = computed(() => {
  return props.trace?.outputs && Object.keys(props.trace.outputs).length > 0;
});

/** 是否有 Token 统计 */
const hasTokenUsage = computed(() => props.trace?.tokenUsage !== undefined);

/** 是否有 HTTP 详情 */
const hasHttpDetails = computed(() => props.trace?.httpDetails !== undefined);

/** 是否有错误信息 */
const hasError = computed(() => props.trace?.error !== undefined);

/** 是否有流式输出 */
const hasStreamingContent = computed(() => !!props.trace?.streamingContent);

/** 默认展开的面板 */
const defaultActiveKey = computed(() => {
  const keys: string[] = [];
  if (hasInputs.value) keys.push('inputs');
  if (hasOutputs.value) keys.push('outputs');
  if (hasError.value) keys.push('error');
  return keys;
});

// ==================== 工具函数 ====================

/** 获取节点图标 */
function getNodeIcon(nodeType: string) {
  const iconMap: Record<string, any> = {
    START: PlayCircleOutlined,
    END: CheckCircleOutlined,
    LLM: RobotOutlined,
    KNOWLEDGE_RETRIEVAL: FileTextOutlined,
    QUESTION_CLASSIFIER: MessageOutlined,
    PARAMETER_EXTRACTOR: FunctionOutlined,
    AGENT: RobotOutlined,
    IF_ELSE: ThunderboltOutlined,
    ITERATION: LoadingOutlined,
    VARIABLE_AGGREGATOR: FunctionOutlined,
    VARIABLE_ASSIGNER: FunctionOutlined,
    LOOP: LoadingOutlined,
    PARALLEL: ThunderboltOutlined,
    CODE: CodeOutlined,
    TEMPLATE: FileTextOutlined,
    DOC_EXTRACTOR: FileTextOutlined,
    LIST_OPERATOR: FunctionOutlined,
    HTTP_REQUEST: ApiOutlined,
    TOOL: FunctionOutlined,
  };
  return iconMap[nodeType] || FunctionOutlined;
}

/** 获取状态颜色 */
function getStatusColor(status: string): string {
  const colorMap: Record<string, string> = {
    pending: 'default',
    running: 'processing',
    completed: 'success',
    failed: 'error',
    skipped: 'default',
  };
  return colorMap[status] || 'default';
}

/** 获取状态图标 */
function getStatusIcon(status: string) {
  const iconMap: Record<string, any> = {
    pending: ClockCircleOutlined,
    running: LoadingOutlined,
    completed: CheckCircleOutlined,
    failed: CloseCircleOutlined,
    skipped: MinusCircleOutlined,
  };
  return iconMap[status] || ClockCircleOutlined;
}

/** 获取状态文本 */
function getStatusText(status: string): string {
  const textMap: Record<string, string> = {
    pending: '等待中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过',
  };
  return textMap[status] || status;
}

/** 格式化耗时 */
function formatDuration(ms: null | number): string {
  if (ms === null) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  return `${(ms / 60_000).toFixed(2)}min`;
}

/** 复制到剪贴板 */
async function copyToClipboard(data: any) {
  try {
    const text =
      typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    await navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  } catch {
    message.error('复制失败');
  }
}

/** 获取 HTTP 状态码颜色 */
function getHttpStatusColor(statusCode: number): string {
  if (statusCode >= 200 && statusCode < 300) return 'success';
  if (statusCode >= 300 && statusCode < 400) return 'warning';
  return 'error';
}

// ==================== 数据展示增强 ====================

/** 搜索关键词 */
const searchKeyword = ref('');

/** 全屏查看弹窗 */
const fullscreenModalVisible = ref(false);
const fullscreenData = ref<{ data: any; title: string }>({
  title: '',
  data: null,
});

/** JSON 展开深度 */
const jsonExpandDepth = ref(3);

/** 是否为简单值（非对象/数组） */
function isSimpleValue(value: any): boolean {
  return value === null || value === undefined || typeof value !== 'object';
}

/** 获取值的类型标签 */
function getValueTypeTag(value: any): { color: string; text: string } {
  if (value === null) return { text: 'null', color: 'default' };
  if (value === undefined) return { text: 'undefined', color: 'default' };
  if (typeof value === 'string') return { text: 'string', color: 'green' };
  if (typeof value === 'number') return { text: 'number', color: 'blue' };
  if (typeof value === 'boolean') return { text: 'boolean', color: 'orange' };
  if (Array.isArray(value))
    return { text: `array[${value.length}]`, color: 'purple' };
  if (typeof value === 'object') return { text: 'object', color: 'cyan' };
  return { text: typeof value, color: 'default' };
}

/** 格式化简单值用于展示 */
function formatSimpleValue(value: any): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') {
    // 处理长字符串
    if (value.length > 200) {
      return `${value.slice(0, 200)}...`;
    }
    return value;
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

/** 将输入数据转换为键值对列表 */
const formattedInputs = computed(() => {
  if (!props.trace?.inputs) return [];
  return Object.entries(props.trace.inputs).map(([key, value]) => ({
    key,
    value,
    isSimple: isSimpleValue(value),
    typeTag: getValueTypeTag(value),
    displayValue: isSimpleValue(value) ? formatSimpleValue(value) : null,
  }));
});

/** 将输出数据转换为键值对列表 */
const formattedOutputs = computed(() => {
  if (!props.trace?.outputs) return [];
  return Object.entries(props.trace.outputs).map(([key, value]) => ({
    key,
    value,
    isSimple: isSimpleValue(value),
    typeTag: getValueTypeTag(value),
    displayValue: isSimpleValue(value) ? formatSimpleValue(value) : null,
  }));
});

/** 过滤后的输入数据 */
const filteredInputs = computed(() => {
  if (!searchKeyword.value) return formattedInputs.value;
  const keyword = searchKeyword.value.toLowerCase();
  return formattedInputs.value.filter(
    (item) =>
      item.key.toLowerCase().includes(keyword) ||
      JSON.stringify(item.value).toLowerCase().includes(keyword),
  );
});

/** 过滤后的输出数据 */
const filteredOutputs = computed(() => {
  if (!searchKeyword.value) return formattedOutputs.value;
  const keyword = searchKeyword.value.toLowerCase();
  return formattedOutputs.value.filter(
    (item) =>
      item.key.toLowerCase().includes(keyword) ||
      JSON.stringify(item.value).toLowerCase().includes(keyword),
  );
});

/** 打开全屏查看 */
function openFullscreen(title: string, data: any) {
  fullscreenData.value = { title, data };
  fullscreenModalVisible.value = true;
}

/** 增加展开深度 */
function increaseDepth() {
  if (jsonExpandDepth.value < 10) {
    jsonExpandDepth.value++;
  }
}

/** 减少展开深度 */
function decreaseDepth() {
  if (jsonExpandDepth.value > 1) {
    jsonExpandDepth.value--;
  }
}
</script>

<template>
  <div class="node-trace-panel">
    <!-- 空状态 -->
    <Empty
      v-if="!hasTrace"
      description="点击已执行的节点查看详情"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />

    <!-- 节点追踪内容 -->
    <template v-else>
      <!-- 节点信息头部 -->
      <div class="trace-header">
        <div class="node-info">
          <component :is="getNodeIcon(trace!.nodeType)" class="node-icon" />
          <span class="node-name">{{ trace!.nodeName }}</span>
          <Tag :color="getStatusColor(trace!.status)">
            <template #icon>
              <component :is="getStatusIcon(trace!.status)" />
            </template>
            {{ getStatusText(trace!.status) }}
          </Tag>
        </div>
        <div class="trace-meta">
          <Tooltip v-if="trace!.duration !== null" title="执行耗时">
            <span class="meta-item">
              <ClockCircleOutlined />
              {{ formatDuration(trace!.duration) }}
            </span>
          </Tooltip>
        </div>
      </div>

      <!-- Token 统计 (LLM 节点) -->
      <div v-if="hasTokenUsage" class="token-stats">
        <div class="stats-title">Token 统计</div>
        <div style="display: flex; gap: 24px">
          <Statistic
            title="输入 Token"
            :value="trace!.tokenUsage!.inputTokens"
          />
          <Statistic
            title="输出 Token"
            :value="trace!.tokenUsage!.outputTokens"
          />
          <Statistic title="总 Token" :value="trace!.tokenUsage!.totalTokens" />
        </div>
      </div>

      <!-- HTTP 详情 (HTTP 节点) -->
      <div v-if="hasHttpDetails" class="http-details">
        <Descriptions :column="2" size="small" bordered title="HTTP 请求详情">
          <DescriptionsItem label="URL">
            <Tooltip :title="trace!.httpDetails!.url">
              <span
                style="
                  max-width: 200px;
                  display: inline-block;
                  overflow: hidden;
                  text-overflow: ellipsis;
                  white-space: nowrap;
                "
              >
                {{ trace!.httpDetails!.url }}
              </span>
            </Tooltip>
          </DescriptionsItem>
          <DescriptionsItem label="方法">
            <Tag color="blue">{{ trace!.httpDetails!.method }}</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="状态码">
            <Tag :color="getHttpStatusColor(trace!.httpDetails!.statusCode)">
              {{ trace!.httpDetails!.statusCode }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="响应时间">
            {{ trace!.httpDetails!.responseTime }}ms
          </DescriptionsItem>
        </Descriptions>
      </div>

      <!-- 搜索框 -->
      <div v-if="hasInputs || hasOutputs" class="search-bar">
        <Input
          v-model:value="searchKeyword"
          placeholder="搜索变量名或值..."
          allow-clear
          size="small"
        >
          <template #prefix>
            <SearchOutlined style="color: #bfbfbf" />
          </template>
        </Input>
      </div>

      <!-- 数据折叠面板 -->
      <Collapse :default-active-key="defaultActiveKey" class="data-collapse">
        <!-- 输入数据 -->
        <CollapsePanel v-if="hasInputs" key="inputs">
          <template #header>
            <div class="panel-header">
              <span class="panel-title">
                <RightOutlined class="panel-icon" />
                输入数据
              </span>
              <Tag size="small" color="blue">
                {{ filteredInputs.length }} 项
              </Tag>
            </div>
          </template>
          <template #extra>
            <div class="panel-actions" @click.stop>
              <Tooltip title="全屏查看">
                <a
                  class="action-btn"
                  @click="openFullscreen('输入数据', trace!.inputs)"
                >
                  <FullscreenOutlined />
                </a>
              </Tooltip>
              <Tooltip title="复制">
                <a class="action-btn" @click="copyToClipboard(trace!.inputs)">
                  <CopyOutlined />
                </a>
              </Tooltip>
            </div>
          </template>

          <!-- 格式化的变量列表 -->
          <div class="variable-list">
            <div
              v-for="item in filteredInputs"
              :key="item.key"
              class="variable-item"
            >
              <div class="variable-header">
                <span class="variable-name">{{ item.key }}</span>
                <Tag :color="item.typeTag.color" size="small">
                  {{ item.typeTag.text }}
                </Tag>
              </div>
              <div class="variable-value">
                <template v-if="item.isSimple">
                  <span
                    class="simple-value"
                    :class="{
                      'null-value':
                        item.value === null || item.value === undefined,
                    }"
                  >
                    {{ item.displayValue }}
                  </span>
                </template>
                <template v-else>
                  <VueJsonPretty
                    :data="item.value"
                    :deep="jsonExpandDepth"
                    :show-length="true"
                    :show-line="false"
                    :show-double-quotes="false"
                  />
                </template>
              </div>
            </div>
            <Empty
              v-if="filteredInputs.length === 0"
              description="无匹配结果"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
            />
          </div>
        </CollapsePanel>

        <!-- 输出数据 -->
        <CollapsePanel v-if="hasOutputs" key="outputs">
          <template #header>
            <div class="panel-header">
              <span class="panel-title">
                <RightOutlined class="panel-icon" />
                输出数据
              </span>
              <Tag size="small" color="green">
                {{ filteredOutputs.length }} 项
              </Tag>
            </div>
          </template>
          <template #extra>
            <div class="panel-actions" @click.stop>
              <Tooltip title="全屏查看">
                <a
                  class="action-btn"
                  @click="openFullscreen('输出数据', trace!.outputs)"
                >
                  <FullscreenOutlined />
                </a>
              </Tooltip>
              <Tooltip title="复制">
                <a class="action-btn" @click="copyToClipboard(trace!.outputs)">
                  <CopyOutlined />
                </a>
              </Tooltip>
            </div>
          </template>

          <!-- 格式化的变量列表 -->
          <div class="variable-list">
            <div
              v-for="item in filteredOutputs"
              :key="item.key"
              class="variable-item"
            >
              <div class="variable-header">
                <span class="variable-name">{{ item.key }}</span>
                <Tag :color="item.typeTag.color" size="small">
                  {{ item.typeTag.text }}
                </Tag>
              </div>
              <div class="variable-value">
                <template v-if="item.isSimple">
                  <span
                    class="simple-value"
                    :class="{
                      'null-value':
                        item.value === null || item.value === undefined,
                    }"
                  >
                    {{ item.displayValue }}
                  </span>
                </template>
                <template v-else>
                  <VueJsonPretty
                    :data="item.value"
                    :deep="jsonExpandDepth"
                    :show-length="true"
                    :show-line="false"
                    :show-double-quotes="false"
                  />
                </template>
              </div>
            </div>
            <Empty
              v-if="filteredOutputs.length === 0"
              description="无匹配结果"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
            />
          </div>
        </CollapsePanel>

        <!-- 流式输出 (LLM 节点) -->
        <CollapsePanel
          v-if="hasStreamingContent"
          key="streaming"
          header="流式输出"
        >
          <div class="streaming-content">
            {{ trace!.streamingContent }}
          </div>
          <Tooltip title="复制">
            <a
              class="copy-btn"
              @click="copyToClipboard(trace!.streamingContent)"
            >
              <CopyOutlined />
            </a>
          </Tooltip>
        </CollapsePanel>

        <!-- 错误信息 -->
        <CollapsePanel v-if="hasError" key="error" header="错误信息">
          <div class="error-content">
            <Alert type="error" show-icon>
              <template #icon>
                <ExclamationCircleOutlined />
              </template>
              <template #message>{{ trace!.error!.type }}</template>
              <template #description>
                <p>{{ trace!.error!.message }}</p>
                <pre v-if="trace!.error!.stackTrace" class="stack-trace">{{
                  trace!.error!.stackTrace
                }}</pre>
              </template>
            </Alert>
            <Tooltip title="复制错误信息">
              <a
                class="copy-btn"
                style="margin-top: 8px"
                @click="copyToClipboard(trace!.error)"
              >
                <CopyOutlined /> 复制错误信息
              </a>
            </Tooltip>
          </div>
        </CollapsePanel>
      </Collapse>
    </template>

    <!-- 全屏查看弹窗 -->
    <Modal
      v-model:open="fullscreenModalVisible"
      :title="fullscreenData.title"
      width="90%"
      :footer="null"
      class="fullscreen-modal"
    >
      <div class="fullscreen-toolbar">
        <div class="depth-control">
          <span class="depth-label">展开深度:</span>
          <a-button
            size="small"
            :disabled="jsonExpandDepth <= 1"
            @click="decreaseDepth"
          >
            <MinusOutlined />
          </a-button>
          <span class="depth-value">{{ jsonExpandDepth }}</span>
          <a-button
            size="small"
            :disabled="jsonExpandDepth >= 10"
            @click="increaseDepth"
          >
            <PlusOutlined />
          </a-button>
        </div>
        <Tooltip title="复制全部">
          <a-button size="small" @click="copyToClipboard(fullscreenData.data)">
            <CopyOutlined /> 复制
          </a-button>
        </Tooltip>
      </div>
      <div class="fullscreen-content">
        <VueJsonPretty
          :data="fullscreenData.data"
          :deep="jsonExpandDepth"
          :show-length="true"
          :show-line="true"
        />
      </div>
    </Modal>
  </div>
</template>

<style lang="less" scoped>
.node-trace-panel {
  height: 100%;
  overflow-y: auto;
  padding: 12px;
  background-color: var(--ant-color-bg-container);

  .trace-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px;
    margin-bottom: 12px;
    background-color: var(--ant-color-bg-layout);
    border-radius: 8px;

    .node-info {
      display: flex;
      gap: 8px;
      align-items: center;

      .node-icon {
        font-size: 18px;
        color: var(--ant-color-primary);
      }

      .node-name {
        font-size: 16px;
        font-weight: 500;
      }
    }

    .trace-meta {
      display: flex;
      gap: 16px;
      align-items: center;

      .meta-item {
        display: flex;
        gap: 4px;
        align-items: center;
        color: var(--ant-color-text-secondary);
        font-size: 13px;
      }
    }
  }

  .token-stats {
    padding: 12px;
    margin-bottom: 12px;
    background-color: var(--ant-color-bg-layout);
    border-radius: 8px;

    .stats-title {
      margin-bottom: 12px;
      color: var(--ant-color-text-secondary);
      font-size: 13px;
    }

    :deep(.ant-statistic) {
      .ant-statistic-title {
        font-size: 12px;
      }

      .ant-statistic-content {
        font-size: 18px;
      }
    }
  }

  .http-details {
    margin-bottom: 12px;

    :deep(.ant-descriptions) {
      .ant-descriptions-item-label {
        font-weight: 500;
      }
    }
  }

  // 搜索栏样式
  .search-bar {
    margin-bottom: 12px;

    :deep(.ant-input-affix-wrapper) {
      border-radius: 6px;
    }
  }

  .data-collapse {
    :deep(.ant-collapse-header) {
      font-weight: 500;
      padding: 10px 12px !important;
    }

    :deep(.ant-collapse-content-box) {
      padding: 0 !important;
    }

    // 面板头部样式
    .panel-header {
      display: flex;
      align-items: center;
      gap: 8px;

      .panel-title {
        display: flex;
        align-items: center;
        gap: 4px;
        font-weight: 500;
      }

      .panel-icon {
        font-size: 10px;
        transition: transform 0.2s;
      }
    }

    // 面板操作按钮
    .panel-actions {
      display: flex;
      gap: 8px;

      .action-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 24px;
        height: 24px;
        color: var(--ant-color-text-secondary);
        border-radius: 4px;
        transition: all 0.2s;

        &:hover {
          color: var(--ant-color-primary);
          background-color: var(--ant-color-primary-bg);
        }
      }
    }

    // 变量列表样式
    .variable-list {
      padding: 8px 12px;
    }

    .variable-item {
      padding: 10px 12px;
      margin-bottom: 8px;
      background-color: var(--ant-color-bg-layout);
      border-radius: 8px;
      border: 1px solid var(--ant-color-border-secondary);
      transition: all 0.2s;

      &:last-child {
        margin-bottom: 0;
      }

      &:hover {
        border-color: var(--ant-color-primary-border);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
      }

      .variable-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 6px;

        .variable-name {
          font-size: 13px;
          font-weight: 600;
          color: var(--ant-color-primary);
          font-family: 'Fira Code', 'Monaco', monospace;
        }
      }

      .variable-value {
        .simple-value {
          display: block;
          padding: 6px 10px;
          font-size: 13px;
          line-height: 1.5;
          color: var(--ant-color-text);
          background-color: var(--ant-color-bg-container);
          border-radius: 4px;
          word-break: break-all;
          white-space: pre-wrap;

          &.null-value {
            color: var(--ant-color-text-quaternary);
            font-style: italic;
          }
        }

        :deep(.vjs-tree) {
          padding: 6px 10px;
          font-size: 12px;
          background-color: var(--ant-color-bg-container);
          border-radius: 4px;
        }
      }
    }

    .json-viewer {
      position: relative;

      .copy-btn {
        position: absolute;
        top: 0;
        right: 0;
      }

      :deep(.vjs-tree) {
        font-size: 13px;
      }
    }

    .streaming-content {
      padding: 12px;
      white-space: pre-wrap;
      word-break: break-word;
      background-color: var(--ant-color-bg-layout);
      border-radius: 4px;
    }

    .error-content {
      padding: 12px;

      .stack-trace {
        max-height: 200px;
        padding: 8px;
        margin-top: 8px;
        overflow: auto;
        font-family: 'Fira Code', monospace;
        font-size: 12px;
        background-color: var(--ant-color-bg-layout);
        border-radius: 4px;
      }
    }
  }
}

// 全屏弹窗样式
.fullscreen-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--ant-color-border);

  .depth-control {
    display: flex;
    align-items: center;
    gap: 8px;

    .depth-label {
      font-size: 13px;
      color: var(--ant-color-text-secondary);
    }

    .depth-value {
      min-width: 24px;
      text-align: center;
      font-weight: 500;
    }
  }
}

.fullscreen-content {
  max-height: 70vh;
  overflow: auto;
  padding: 12px;
  background-color: var(--ant-color-bg-layout);
  border-radius: 8px;

  :deep(.vjs-tree) {
    font-size: 13px;
  }
}

// 暗色模式适配
html[class='dark'] {
  .node-trace-panel {
    .trace-header,
    .token-stats {
      background-color: var(--ant-color-bg-elevated);
    }

    :deep(.vjs-tree-node:hover) {
      background-color: #333;
    }
  }
}
</style>
