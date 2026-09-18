<script setup lang="ts">
/**
 * DebugPanel 调试面板组件
 * 采用可调整大小的分栏布局，集成 PreviewRunner、NodeTracePanel、VariableInspector
 *
 * 审批入口不在本面板：暂停唯一来自 APPROVAL 节点，由 PreviewRunner 内的审批面板
 * 经 submit-sync 回传结论（断点与无条件 resume 链路已整体废弃）。
 */
import type { InputField } from '#/api/ai-workflow/types';
import type {
  NodeExecutionStatus,
  NodeTrace,
  VariableGroup,
} from '#/store/debug-store';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import {
  BugOutlined,
  ClockCircleOutlined,
  ColumnHeightOutlined,
  ColumnWidthOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

import NodeTracePanel from './debug/NodeTracePanel.vue';
import PreviewRunner from './debug/PreviewRunner.vue';
import VariableInspector from './debug/VariableInspector.vue';

// ==================== Props ====================

interface Props {
  /** 工作流ID */
  workflowId?: string;
  /** 输入字段定义 */
  inputFields?: InputField[];
  /** 布局模式: bottom-底部抽屉, right-右侧抽屉 */
  layoutMode?: 'bottom' | 'right';
}

const props = withDefaults(defineProps<Props>(), {
  workflowId: '',
  inputFields: () => [],
  layoutMode: 'right',
});

// ==================== Emits ====================

const emit = defineEmits<{
  /** 布局模式变化 */
  (e: 'layout-change', mode: 'bottom' | 'right'): void;
  /** 节点点击 */
  (e: 'node-click', nodeId: string): void;
  /** 关闭面板 */
  (e: 'close'): void;
}>();

// ==================== Store ====================

const debugStore = useDebugStore();
const workflowStore = useAiWorkflowStore();

// ==================== State ====================

/** 当前激活的标签页 */
const activeTab = ref<'runner' | 'trace' | 'variables'>('runner');

/** 分栏大小 (百分比) */
const splitSize = ref(50);

/** 是否正在拖拽分隔条 */
const isDragging = ref(false);

/** 选中的节点追踪数据 */
const selectedTrace = ref<NodeTrace | null>(null);

/** PreviewRunner 组件引用 */
const previewRunnerRef = ref<InstanceType<typeof PreviewRunner>>();

// ==================== Computed ====================

/** 是否处于调试模式 */
const isDebugMode = computed({
  get: () => workflowStore.isDebugMode,
  set: (value) => {
    workflowStore.isDebugMode = value;
  },
});

/** 是否正在执行 */
const isRunning = computed(() => debugStore.isRunning);

/** 是否已暂停（等待人工审批） */
const isPaused = computed(() => debugStore.isPaused);

/** 变量分组列表 */
const variableGroups = computed(
  (): VariableGroup[] => debugStore.variableGroups,
);

/** 节点追踪列表 */
const nodeTraces = computed(() => debugStore.sortedNodeTraces);

/** 当前节点ID */
const currentNodeId = computed(() => debugStore.currentNodeId);

/** 布局是否为水平 (底部抽屉) */
const isHorizontalLayout = computed(() => props.layoutMode === 'bottom');

/** 节点详情子面板仅在「节点追踪」Tab 显示，其他 Tab 占满整栏 */
const showNodeDetail = computed(() => activeTab.value === 'trace');

/** 需要同步到画布的节点状态（awaiting=审批挂起、skipped=恢复提交里本轮沿用） */
type CanvasSyncStatus =
  | 'cancelled'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'running'
  | 'skipped'
  | 'timeout';

const CANVAS_SYNC_STATUSES = new Set<string>([
  'awaiting',
  'cancelled',
  'completed',
  'failed',
  'running',
  'skipped',
  'timeout',
]);

/** trace 状态 → 画布高亮入参：审批挂起在运行时叫 AWAITING，画布侧统一走 paused */
function toCanvasStatus(status: string): CanvasSyncStatus {
  return status === 'awaiting' ? 'paused' : (status as CanvasSyncStatus);
}

// ==================== Lifecycle ====================

onMounted(() => {
  // 设置快捷键
  setupKeyboardShortcuts();
});

onBeforeUnmount(() => {
  // 移除快捷键监听
  removeKeyboardShortcuts();
});

// ==================== Watch ====================

// 监听当前节点变化，自动选中追踪数据
watch(currentNodeId, (nodeId) => {
  if (nodeId) {
    const trace = debugStore.getNodeTrace(nodeId);
    if (trace) {
      selectedTrace.value = trace;
      activeTab.value = 'trace';
    }
  }
});

// 监听节点追踪变化
watch(
  nodeTraces,
  (traces) => {
    // 如果当前选中的追踪数据有更新，刷新它
    if (selectedTrace.value) {
      const updated = traces.find(
        (t) => t.nodeId === selectedTrace.value?.nodeId,
      );
      if (updated) {
        selectedTrace.value = updated;
      }
    }
  },
  { deep: true },
);

// 预览运行：节点状态与耗时同步到画布
watch(
  nodeTraces,
  (traces) => {
    traces.forEach((trace) => {
      if (CANVAS_SYNC_STATUSES.has(trace.status)) {
        workflowStore.highlightNode(
          trace.nodeId,
          toCanvasStatus(trace.status),
          trace.duration ?? undefined,
        );
      }
    });
  },
  { deep: true },
);

// 新一次预览运行开始时清除画布旧高亮
watch(isRunning, (running) => {
  if (running) {
    workflowStore.resetNodeHighlights();
  }
});

// ==================== Methods ====================

/**
 * 设置快捷键。
 */
function setupKeyboardShortcuts() {
  window.addEventListener('keydown', handleKeyDown);
}

/**
 * 移除快捷键监听
 */
function removeKeyboardShortcuts() {
  window.removeEventListener('keydown', handleKeyDown);
}

/**
 * 处理快捷键
 */
function handleKeyDown(e: KeyboardEvent) {
  // 只在调试模式下响应
  if (!isDebugMode.value) return;

  if (e.key === 'F5') {
    // F5 执行
    e.preventDefault();
    if (!isRunning.value && previewRunnerRef.value) {
      previewRunnerRef.value.run();
    }
  }
}

/**
 * 切换布局模式
 */
function toggleLayoutMode() {
  const newMode = props.layoutMode === 'bottom' ? 'right' : 'bottom';
  emit('layout-change', newMode);
}

/**
 * 处理分隔条拖拽开始
 */
function handleSplitDragStart(e: MouseEvent) {
  isDragging.value = true;
  e.preventDefault();
  document.addEventListener('mousemove', handleSplitDragMove);
  document.addEventListener('mouseup', handleSplitDragEnd);
}

/**
 * 处理分隔条拖拽移动
 */
function handleSplitDragMove(e: MouseEvent) {
  if (!isDragging.value) return;

  const container = document.querySelector(
    '.debug-panel-content',
  ) as HTMLElement;
  if (!container) return;

  const rect = container.getBoundingClientRect();
  let newSize: number;

  if (isHorizontalLayout.value) {
    // 水平布局，计算左右比例
    newSize = ((e.clientX - rect.left) / rect.width) * 100;
  } else {
    // 垂直布局，计算上下比例
    newSize = ((e.clientY - rect.top) / rect.height) * 100;
  }

  // 限制范围 20% - 80%
  splitSize.value = Math.max(20, Math.min(80, newSize));
}

/**
 * 处理分隔条拖拽结束
 */
function handleSplitDragEnd() {
  isDragging.value = false;
  document.removeEventListener('mousemove', handleSplitDragMove);
  document.removeEventListener('mouseup', handleSplitDragEnd);
}

/**
 * 选择节点追踪
 */
function handleSelectTrace(trace: NodeTrace) {
  selectedTrace.value = trace;
  activeTab.value = 'trace';
}

/**
 * 处理节点点击 (从追踪面板)
 */
function handleNodeClick(nodeId: string) {
  emit('node-click', nodeId);
}

/** 追踪状态中文标签（awaiting=待审批、timeout=已超时、cancelled=已取消） */
function traceStatusText(status: NodeExecutionStatus): string {
  const labels: Record<string, string> = {
    awaiting: '待审批',
    cancelled: '已取消',
    completed: '已完成',
    failed: '失败',
    pending: '等待中',
    running: '执行中',
    skipped: '已跳过',
    timeout: '已超时',
  };
  return labels[status] || status;
}

/** 追踪状态 Tag 颜色（timeout 用黄色告警，awaiting 用紫色，区别于蓝色执行中） */
function traceStatusColor(status: NodeExecutionStatus): string {
  const colors: Record<string, string> = {
    awaiting: 'purple',
    cancelled: 'default',
    completed: 'success',
    failed: 'error',
    running: 'processing',
    skipped: 'default',
    timeout: 'warning',
  };
  return colors[status] || 'default';
}

/**
 * 处理调试变量编辑。
 *
 * 变量编辑不再直连后端（原 update_variable 已随外部暂停链路废弃）：
 * VariableInspector 已自行维护本地视图，能影响执行的改动必须走审批表单的 edits。
 */
function handleVariableUpdate(
  _nodeId: string,
  _variableName: string,
  _newValue: any,
) {
  // 仅保留事件接入，避免子组件回调签名变动
}

// ==================== Expose ====================

defineExpose({
  /** 选择节点追踪 */
  selectTrace: handleSelectTrace,
  /** 设置预览运行输入值 */
  setRunnerInputValues: (values: Record<string, any>) => {
    previewRunnerRef.value?.setInputValues(values);
  },
  /** 启动预览运行 */
  runPreview: () => previewRunnerRef.value?.run(),
  /** 获取预览运行输入值 */
  getRunnerInputValues: () => previewRunnerRef.value?.getInputValues() || {},
  /** 获取执行完成结果 */
  getExecutionResult: () =>
    previewRunnerRef.value?.getExecutionResult() || debugStore.result,
  /** 获取节点追踪 */
  getNodeTraces: () =>
    previewRunnerRef.value?.getNodeTraces() || [
      ...debugStore.nodeTraces.values(),
    ],
  /** 切换到指定标签页 */
  switchTab: (tab: 'runner' | 'trace' | 'variables') => {
    activeTab.value = tab;
  },
  /**
   * 重置面板（BUG5）：回到「预览运行」页并清空上一次的输入，
   * 执行结果/节点追踪/变量等状态由 debugStore.clearExecutionState() 负责清除
   */
  resetPanel: () => {
    selectedTrace.value = null;
    activeTab.value = 'runner';
    previewRunnerRef.value?.reset();
  },
});
</script>

<template>
  <div
    class="debug-panel"
    data-testid="workflow-debug-panel"
    :class="{
      'layout-horizontal': isHorizontalLayout,
      'layout-vertical': !isHorizontalLayout,
      dragging: isDragging,
    }"
  >
    <!-- 调试面板头部 -->
    <div class="debug-panel-header">
      <div class="header-left">
        <BugOutlined class="header-icon" />
        <span class="header-title">调试面板</span>
        <a-tag v-if="isRunning" color="processing" size="small">执行中</a-tag>
        <a-tag v-else-if="isPaused" color="purple" size="small"> 待审批 </a-tag>
      </div>
      <div class="header-right">
        <!-- 布局切换 -->
        <a-tooltip
          :title="isHorizontalLayout ? '切换为右侧布局' : '切换为底部布局'"
        >
          <a-button type="text" size="small" @click="toggleLayoutMode">
            <template #icon>
              <ColumnWidthOutlined v-if="isHorizontalLayout" />
              <ColumnHeightOutlined v-else />
            </template>
          </a-button>
        </a-tooltip>
        <!-- 帮助 -->
        <a-tooltip title="快捷键: F5 执行">
          <a-button type="text" size="small">
            <template #icon><QuestionCircleOutlined /></template>
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <!-- 调试面板内容 -->
    <div class="debug-panel-content">
      <!-- 左侧/上方区域: 标签页 -->
      <div
        class="panel-section panel-primary"
        :style="
          isHorizontalLayout
            ? { width: showNodeDetail ? `${splitSize}%` : '100%' }
            : { height: showNodeDetail ? `${splitSize}%` : '100%' }
        "
      >
        <!-- 标签页导航 -->
        <a-tabs v-model:active-key="activeTab" size="small" class="panel-tabs">
          <a-tab-pane key="runner" tab="预览运行">
            <PreviewRunner
              ref="previewRunnerRef"
              :workflow-id="workflowId"
              :input-fields="inputFields"
              @node-start="
                (nodeId: string) =>
                  handleSelectTrace(debugStore.getNodeTrace(nodeId)!)
              "
            />
          </a-tab-pane>
          <a-tab-pane key="trace">
            <template #tab>
              <span>
                节点追踪
                <a-badge
                  v-if="nodeTraces.length > 0"
                  :count="nodeTraces.length"
                  :number-style="{
                    backgroundColor: '#1890ff',
                    fontSize: '10px',
                  }"
                />
              </span>
            </template>
            <div class="trace-section">
              <!-- 节点列表 -->
              <div class="trace-list">
                <a-empty
                  v-if="nodeTraces.length === 0"
                  description="暂无执行记录"
                />
                <div
                  v-for="trace in nodeTraces"
                  :key="trace.nodeId"
                  class="trace-item"
                  :data-node-id="trace.nodeId"
                  :data-node-type="trace.nodeType"
                  :class="{
                    active: selectedTrace?.nodeId === trace.nodeId,
                    running: trace.status === 'running',
                    completed: trace.status === 'completed',
                    failed: trace.status === 'failed',
                    timeout: trace.status === 'timeout',
                    cancelled: trace.status === 'cancelled',
                    awaiting: trace.status === 'awaiting',
                    skipped: trace.status === 'skipped',
                  }"
                  @click="handleSelectTrace(trace)"
                >
                  <div class="trace-item-header">
                    <span class="trace-name">{{ trace.nodeName }}</span>
                    <a-tag :color="traceStatusColor(trace.status)" size="small">
                      {{ traceStatusText(trace.status) }}
                    </a-tag>
                  </div>
                  <!-- 0ms 也是有效耗时（轻量节点常 sub-ms），只排除尚未执行的 null -->
                  <div v-if="trace.duration !== null" class="trace-item-meta">
                    <ClockCircleOutlined />
                    {{ trace.duration }}ms
                  </div>
                </div>
              </div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="variables">
            <template #tab>
              <span>
                变量
                <a-badge
                  v-if="variableGroups.length > 0"
                  :count="
                    variableGroups.reduce(
                      (sum, g) => sum + g.variables.length,
                      0,
                    )
                  "
                  :number-style="{
                    backgroundColor: '#52c41a',
                    fontSize: '10px',
                  }"
                />
              </span>
            </template>
            <VariableInspector
              :groups="variableGroups"
              :is-paused="isPaused"
              :execution-id="debugStore.executionId"
              @update="handleVariableUpdate"
            />
          </a-tab-pane>
        </a-tabs>
      </div>

      <!-- 分隔条 -->
      <div
        v-if="showNodeDetail"
        class="panel-splitter"
        :class="{ horizontal: isHorizontalLayout }"
        @mousedown="handleSplitDragStart"
      >
        <div class="splitter-handle"></div>
      </div>

      <!-- 右侧/下方区域: 节点详情（仅节点追踪 Tab 显示） -->
      <div
        v-if="showNodeDetail"
        class="panel-section panel-secondary"
        :style="
          isHorizontalLayout
            ? { width: `${100 - splitSize}%` }
            : { height: `${100 - splitSize}%` }
        "
      >
        <div class="section-header">
          <span class="section-title">节点详情</span>
          <a-button
            v-if="selectedTrace"
            type="link"
            size="small"
            @click="handleNodeClick(selectedTrace.nodeId)"
          >
            定位到画布
          </a-button>
        </div>
        <NodeTracePanel :trace="selectedTrace" />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.debug-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background-color: var(--ant-color-bg-container);

  &.dragging {
    cursor: col-resize;
    user-select: none;

    &.layout-vertical {
      cursor: row-resize;
    }
  }
}

.debug-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ant-color-border);
  background-color: var(--ant-color-bg-layout);

  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;

    .header-icon {
      font-size: 16px;
      color: var(--ant-color-primary);
    }

    .header-title {
      font-size: 14px;
      font-weight: 500;
    }
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 4px;

    .debug-controls {
      margin-right: 8px;
    }
  }
}

.debug-panel-content {
  display: flex;
  flex: 1;
  overflow: hidden;

  .layout-horizontal & {
    flex-direction: row;
  }

  .layout-vertical & {
    flex-direction: column;
  }
}

.panel-section {
  display: flex;
  flex-direction: column;
  overflow: hidden;

  &.panel-primary {
    min-width: 200px;
    min-height: 150px;
  }

  &.panel-secondary {
    min-width: 200px;
    min-height: 150px;
    background-color: var(--ant-color-bg-layout);
  }
}

.panel-splitter {
  position: relative;
  flex-shrink: 0;
  background-color: var(--ant-color-border);
  transition: background-color 0.2s;

  &:hover {
    background-color: var(--ant-color-primary);
  }

  .layout-horizontal & {
    width: 4px;
    cursor: col-resize;
  }

  .layout-vertical & {
    height: 4px;
    cursor: row-resize;
  }

  .splitter-handle {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);

    .layout-horizontal & {
      width: 2px;
      height: 24px;
      background-color: var(--ant-color-text-quaternary);
      border-radius: 1px;
    }

    .layout-vertical & {
      width: 24px;
      height: 2px;
      background-color: var(--ant-color-text-quaternary);
      border-radius: 1px;
    }
  }
}

.panel-tabs {
  height: 100%;

  :deep(.ant-tabs-content) {
    height: 100%;
  }

  :deep(.ant-tabs-tabpane) {
    height: 100%;
    overflow: auto;
  }
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ant-color-border);

  .section-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--ant-color-text-secondary);
  }
}

// 节点追踪列表样式
.trace-section {
  height: 100%;
  overflow: hidden;
}

.trace-list {
  height: 100%;
  overflow-y: auto;
  padding: 8px;
}

.trace-item {
  padding: 8px 12px;
  margin-bottom: 4px;
  cursor: pointer;
  border-radius: 6px;
  border: 1px solid transparent;
  transition: all 0.2s;

  &:hover {
    background-color: var(--ant-color-bg-layout);
  }

  &.active {
    background-color: var(--ant-color-primary-bg);
    border-color: var(--ant-color-primary);
  }

  &.running {
    border-left: 3px solid var(--ant-color-primary);
  }

  &.completed {
    border-left: 3px solid var(--ant-color-success);
  }

  &.failed {
    border-left: 3px solid var(--ant-color-error);
  }

  // 并行分支等待超时：黄色背景告警
  &.timeout {
    background-color: #fffbe6;
    border-left: 3px solid #faad14;
  }

  // 并行分支被其他分支先完成短路：置灰
  &.cancelled {
    border-left: 3px solid #d9d9d9;
    opacity: 0.7;
  }

  // 审批节点挂起：紫色等待态
  &.awaiting {
    background-color: #f9f0ff;
    border-left: 3px solid #722ed1;
  }

  // 恢复提交里本轮沿用的已完成节点：置灰不加粗
  &.skipped {
    border-left: 3px solid #d9d9d9;
    opacity: 0.75;
  }

  .trace-item-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;

    .trace-name {
      font-size: 13px;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .trace-item-meta {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--ant-color-text-secondary);
  }
}

// 暗色模式适配
html[class='dark'] {
  .debug-panel-header {
    background-color: var(--ant-color-bg-elevated);
  }

  .panel-section.panel-secondary {
    background-color: var(--ant-color-bg-elevated);
  }

  .trace-item {
    &:hover {
      background-color: var(--ant-color-bg-elevated);
    }

    &.active {
      background-color: rgba(24, 144, 255, 0.15);
    }
  }
}
</style>
