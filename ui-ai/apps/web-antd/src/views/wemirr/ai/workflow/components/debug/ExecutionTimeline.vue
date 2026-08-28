<script setup lang="ts">
/**
 * 执行时间线组件 (Coze-Style)
 * 以甘特图形式展示工作流节点的执行时间线
 *
 */

import type { TimelineItem } from '#/store/debug-store';

import { computed, ref, watch } from 'vue';

import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  FileTextOutlined,
  FunctionOutlined,
  LoadingOutlined,
  MessageOutlined,
  MinusCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons-vue';
import { Button, ButtonGroup, Empty, Tooltip } from 'ant-design-vue';

// ==================== Props ====================

interface Props {
  /** 时间线项列表 */
  items: TimelineItem[];
  /** 总执行时间(毫秒) */
  totalDuration: number;
}

const props = defineProps<Props>();

// ==================== Emits ====================

const emit = defineEmits<{
  /** 点击节点时触发，用于跳转到画布节点 */
  (e: 'nodeClick', nodeId: string): void;
}>();

// ==================== 状态 ====================

/** 缩放比例 (1 = 100%) */
const scale = ref(1);

/** 水平偏移量 */
const offsetX = ref(0);

/** 当前悬停的项 */
const hoveredItem = ref<null | TimelineItem>(null);

/** 容器引用 */
const containerRef = ref<HTMLElement | null>(null);

/** 是否正在拖拽 */
const isDragging = ref(false);

/** 拖拽起始位置 */
const dragStartX = ref(0);

/** 拖拽起始偏移 */
const dragStartOffset = ref(0);

// ==================== 常量 ====================

/** 最小缩放比例 */
const MIN_SCALE = 0.5;

/** 最大缩放比例 */
const MAX_SCALE = 3;

/** 缩放步长 */
const SCALE_STEP = 0.25;

/** 时间条高度 */
const BAR_HEIGHT = 28;

/** 时间条间距 */
const BAR_GAP = 8;

/** 时间轴高度 */
const RULER_HEIGHT = 32;

// ==================== 计算属性 ====================

/** 是否有数据 */
const hasItems = computed(() => props.items.length > 0);

/** 计算时间刻度 */
const timeTicks = computed(() => {
  if (props.totalDuration <= 0) return [];

  const ticks: { position: number; value: number }[] = [];
  const duration = props.totalDuration;

  // 根据总时长决定刻度间隔
  let interval: number;
  if (duration <= 1000) {
    interval = 100; // 100ms
  } else if (duration <= 5000) {
    interval = 500; // 500ms
  } else if (duration <= 30_000) {
    interval = 2000; // 2s
  } else if (duration <= 60_000) {
    interval = 5000; // 5s
  } else {
    interval = 10_000; // 10s
  }

  for (let t = 0; t <= duration; t += interval) {
    ticks.push({
      value: t,
      position: (t / duration) * 100,
    });
  }

  // 确保最后一个刻度
  if (ticks.length > 0 && ticks[ticks.length - 1]!.value < duration) {
    ticks.push({
      value: duration,
      position: 100,
    });
  }

  return ticks;
});

/** 计算时间线内容高度 */
const contentHeight = computed(() => {
  return props.items.length * (BAR_HEIGHT + BAR_GAP) + BAR_GAP;
});

/** 计算缩放后的宽度 */
const scaledWidth = computed(() => {
  return 100 * scale.value;
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
    pending: '#d9d9d9',
    running: '#1890ff',
    completed: '#52c41a',
    failed: '#ff4d4f',
    skipped: '#d9d9d9',
  };
  return colorMap[status] || '#d9d9d9';
}

/** 获取状态背景色 */
function getStatusBgColor(status: string): string {
  const colorMap: Record<string, string> = {
    pending: '#f5f5f5',
    running: '#e6f7ff',
    completed: '#f6ffed',
    failed: '#fff2f0',
    skipped: '#f5f5f5',
  };
  return colorMap[status] || '#f5f5f5';
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

/** 格式化时间 */
function formatTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}min`;
}

/** 计算时间条样式 */
function getBarStyle(item: TimelineItem) {
  if (props.totalDuration <= 0) {
    return {
      left: '0%',
      width: '100%',
    };
  }

  const left = (item.startTime / props.totalDuration) * 100;
  const width = Math.max((item.duration / props.totalDuration) * 100, 2); // 最小宽度 2%

  return {
    left: `${left}%`,
    width: `${width}%`,
    backgroundColor: getStatusBgColor(item.status),
    borderColor: getStatusColor(item.status),
  };
}

// ==================== 交互处理 ====================

/** 放大 */
function zoomIn() {
  if (scale.value < MAX_SCALE) {
    scale.value = Math.min(scale.value + SCALE_STEP, MAX_SCALE);
  }
}

/** 缩小 */
function zoomOut() {
  if (scale.value > MIN_SCALE) {
    scale.value = Math.max(scale.value - SCALE_STEP, MIN_SCALE);
  }
}

/** 重置缩放 */
function resetZoom() {
  scale.value = 1;
  offsetX.value = 0;
}

/** 处理鼠标滚轮缩放 */
function handleWheel(e: WheelEvent) {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    if (e.deltaY < 0) {
      zoomIn();
    } else {
      zoomOut();
    }
  }
}

/** 开始拖拽 */
function handleMouseDown(e: MouseEvent) {
  if (scale.value > 1) {
    isDragging.value = true;
    dragStartX.value = e.clientX;
    dragStartOffset.value = offsetX.value;
    e.preventDefault();
  }
}

/** 拖拽中 */
function handleMouseMove(e: MouseEvent) {
  if (isDragging.value && containerRef.value) {
    const deltaX = e.clientX - dragStartX.value;
    const containerWidth = containerRef.value.clientWidth;
    const maxOffset = containerWidth * (scale.value - 1);
    offsetX.value = Math.max(
      -maxOffset,
      Math.min(0, dragStartOffset.value + deltaX),
    );
  }
}

/** 结束拖拽 */
function handleMouseUp() {
  isDragging.value = false;
}

/** 处理时间条点击 */
function handleBarClick(item: TimelineItem) {
  emit('nodeClick', item.nodeId);
}

/** 处理鼠标进入时间条 */
function handleBarMouseEnter(item: TimelineItem) {
  hoveredItem.value = item;
}

/** 处理鼠标离开时间条 */
function handleBarMouseLeave() {
  hoveredItem.value = null;
}

// ==================== 监听 ====================

// 当缩放比例变化时，限制偏移量
watch(scale, (newScale) => {
  if (newScale <= 1) {
    offsetX.value = 0;
  } else if (containerRef.value) {
    const containerWidth = containerRef.value.clientWidth;
    const maxOffset = containerWidth * (newScale - 1);
    offsetX.value = Math.max(-maxOffset, Math.min(0, offsetX.value));
  }
});
</script>

<template>
  <div
    ref="containerRef"
    class="execution-timeline"
    :class="{ dragging: isDragging }"
    @wheel="handleWheel"
    @mousedown="handleMouseDown"
    @mousemove="handleMouseMove"
    @mouseup="handleMouseUp"
    @mouseleave="handleMouseUp"
  >
    <!-- 空状态 -->
    <Empty
      v-if="!hasItems"
      description="暂无执行数据"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />

    <!-- 时间线内容 -->
    <template v-else>
      <!-- 缩放控制 -->
      <div class="timeline-controls">
        <ButtonGroup size="small">
          <Tooltip title="放大 (Ctrl+滚轮)">
            <Button :disabled="scale >= MAX_SCALE" @click="zoomIn">
              <ZoomInOutlined />
            </Button>
          </Tooltip>
          <Tooltip title="缩小 (Ctrl+滚轮)">
            <Button :disabled="scale <= MIN_SCALE" @click="zoomOut">
              <ZoomOutOutlined />
            </Button>
          </Tooltip>
          <Tooltip title="重置">
            <Button @click="resetZoom">重置</Button>
          </Tooltip>
        </ButtonGroup>
        <span class="scale-indicator">{{ Math.round(scale * 100) }}%</span>
      </div>

      <!-- 时间线容器 -->
      <div class="timeline-container">
        <div
          class="timeline-content"
          :style="{
            width: `${scaledWidth}%`,
            transform: `translateX(${offsetX}px)`,
          }"
        >
          <!-- 时间轴刻度 -->
          <div class="timeline-ruler" :style="{ height: `${RULER_HEIGHT}px` }">
            <div
              v-for="tick in timeTicks"
              :key="tick.value"
              class="tick"
              :style="{ left: `${tick.position}%` }"
            >
              <span class="tick-label">{{ formatTime(tick.value) }}</span>
              <span class="tick-line"></span>
            </div>
          </div>

          <!-- 节点时间条 -->
          <div class="timeline-bars" :style="{ height: `${contentHeight}px` }">
            <Tooltip
              v-for="(item, index) in items"
              :key="item.nodeId"
              placement="top"
            >
              <template #title>
                <div class="timeline-tooltip">
                  <div class="tooltip-header">
                    <component
                      :is="getNodeIcon(item.nodeType)"
                      class="tooltip-icon"
                    />
                    <strong>{{ item.nodeName }}</strong>
                  </div>
                  <div class="tooltip-content">
                    <p>
                      <span class="label">类型:</span>
                      {{ item.nodeType }}
                    </p>
                    <p>
                      <span class="label">开始:</span>
                      {{ formatTime(item.startTime) }}
                    </p>
                    <p>
                      <span class="label">结束:</span>
                      {{ formatTime(item.endTime) }}
                    </p>
                    <p>
                      <span class="label">耗时:</span>
                      {{ formatTime(item.duration) }}
                    </p>
                    <p>
                      <span class="label">状态:</span>
                      <component
                        :is="getStatusIcon(item.status)"
                        :style="{ color: getStatusColor(item.status) }"
                      />
                      {{ item.status }}
                    </p>
                  </div>
                </div>
              </template>
              <div
                class="timeline-bar"
                :class="[`status-${item.status}`]"
                :style="{
                  ...getBarStyle(item),
                  top: `${index * (BAR_HEIGHT + BAR_GAP) + BAR_GAP}px`,
                  height: `${BAR_HEIGHT}px`,
                }"
                @click="handleBarClick(item)"
                @mouseenter="handleBarMouseEnter(item)"
                @mouseleave="handleBarMouseLeave"
              >
                <span class="bar-icon">
                  <component :is="getNodeIcon(item.nodeType)" />
                </span>
                <span class="bar-label">{{ item.nodeName }}</span>
                <span class="bar-duration">{{
                  formatTime(item.duration)
                }}</span>
              </div>
            </Tooltip>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style lang="less" scoped>
.execution-timeline {
  position: relative;
  height: 100%;
  overflow: hidden;
  background-color: var(--ant-color-bg-container);
  border-radius: 8px;

  &.dragging {
    cursor: grabbing;
    user-select: none;
  }

  // 缩放控制
  .timeline-controls {
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 10;
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 4px 8px;
    background-color: var(--ant-color-bg-container);
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);

    .scale-indicator {
      min-width: 40px;
      color: var(--ant-color-text-secondary);
      font-size: 12px;
      text-align: center;
    }
  }

  // 时间线容器
  .timeline-container {
    height: 100%;
    padding: 48px 16px 16px;
    overflow-x: hidden;
    overflow-y: auto;
  }

  // 时间线内容
  .timeline-content {
    position: relative;
    min-width: 100%;
    transition: transform 0.1s ease-out;
  }

  // 时间轴刻度
  .timeline-ruler {
    position: relative;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--ant-color-border);

    .tick {
      position: absolute;
      bottom: 0;
      transform: translateX(-50%);

      .tick-label {
        display: block;
        margin-bottom: 4px;
        color: var(--ant-color-text-secondary);
        font-size: 11px;
        white-space: nowrap;
      }

      .tick-line {
        display: block;
        width: 1px;
        height: 8px;
        margin: 0 auto;
        background-color: var(--ant-color-border);
      }
    }
  }

  // 节点时间条容器
  .timeline-bars {
    position: relative;
    min-height: 100px;
  }

  // 节点时间条
  .timeline-bar {
    position: absolute;
    display: flex;
    gap: 6px;
    align-items: center;
    padding: 0 8px;
    overflow: hidden;
    cursor: pointer;
    border: 1px solid;
    border-radius: 4px;
    transition: all 0.2s ease;

    &:hover {
      transform: translateY(-1px);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    }

    .bar-icon {
      flex-shrink: 0;
      font-size: 14px;
      opacity: 0.8;
    }

    .bar-label {
      flex: 1;
      overflow: hidden;
      font-size: 12px;
      font-weight: 500;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .bar-duration {
      flex-shrink: 0;
      color: var(--ant-color-text-secondary);
      font-size: 11px;
    }

    // 状态样式
    &.status-pending {
      background-color: #f5f5f5;
      border-color: #d9d9d9;

      .bar-icon {
        color: #8c8c8c;
      }
    }

    &.status-running {
      background-color: #e6f7ff;
      border-color: #1890ff;

      .bar-icon {
        color: #1890ff;
        animation: spin 1s linear infinite;
      }
    }

    &.status-completed {
      background-color: #f6ffed;
      border-color: #52c41a;

      .bar-icon {
        color: #52c41a;
      }
    }

    &.status-failed {
      background-color: #fff2f0;
      border-color: #ff4d4f;

      .bar-icon {
        color: #ff4d4f;
      }
    }

    &.status-skipped {
      background-color: #f5f5f5;
      border-color: #d9d9d9;
      opacity: 0.6;

      .bar-icon {
        color: #8c8c8c;
      }
    }
  }
}

// Tooltip 样式
.timeline-tooltip {
  .tooltip-header {
    display: flex;
    gap: 6px;
    align-items: center;
    padding-bottom: 8px;
    margin-bottom: 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.2);

    .tooltip-icon {
      font-size: 16px;
    }
  }

  .tooltip-content {
    p {
      margin: 4px 0;
      font-size: 12px;

      .label {
        display: inline-block;
        min-width: 40px;
        color: rgba(255, 255, 255, 0.7);
      }
    }
  }
}

// 旋转动画
@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

// 暗色模式适配
html[class='dark'] {
  .execution-timeline {
    .timeline-controls {
      background-color: var(--ant-color-bg-elevated);
    }

    .timeline-bar {
      &.status-pending {
        background-color: #262626;
        border-color: #434343;
      }

      &.status-running {
        background-color: #111d2c;
        border-color: #177ddc;
      }

      &.status-completed {
        background-color: #162312;
        border-color: #49aa19;
      }

      &.status-failed {
        background-color: #2a1215;
        border-color: #d32029;
      }

      &.status-skipped {
        background-color: #262626;
        border-color: #434343;
      }
    }
  }
}
</style>
