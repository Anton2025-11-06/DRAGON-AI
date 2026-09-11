<script setup lang="ts">
/**
 * DebugPanel 调试面板组件
 * 采用可调整大小的分栏布局，集成 PreviewRunner、NodeTracePanel、VariableInspector、CheckpointManager
 */
import type { InputField } from '#/api/ai-workflow/types';
import type { NodeTrace, VariableGroup } from '#/store/debug-store';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import {
  BugOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  ColumnHeightOutlined,
  ColumnWidthOutlined,
  DeleteOutlined,
  FastForwardOutlined,
  InfoCircleOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue';
import { message, Modal } from 'ant-design-vue';

import { resumeExecution, updateVariable } from '#/api/ai-workflow';
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
const activeTab = ref<'breakpoints' | 'runner' | 'trace' | 'variables'>(
  'runner',
);

/** 分栏大小 (百分比) */
const splitSize = ref(50);

/** 是否正在拖拽分隔条 */
const isDragging = ref(false);

/** 选中的节点追踪数据 */
const selectedTrace = ref<NodeTrace | null>(null);

/** 条件检查点编辑弹窗 */
const conditionModalVisible = ref(false);
const editingBreakpointId = ref<null | string>(null);
const editingCondition = ref('');

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

/** 是否已暂停 */
const isPaused = computed(() => debugStore.isPaused);

/** 检查点列表 */
const breakpointList = computed(() => [...debugStore.breakpoints.values()]);

/** 检查点数量 */
const breakpointCount = computed(() => debugStore.breakpointCount);

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
      if (
        trace.status === 'completed' ||
        trace.status === 'failed' ||
        trace.status === 'running'
      ) {
        workflowStore.highlightNode(
          trace.nodeId,
          trace.status,
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

  switch (e.key) {
    case 'F5': {
      // F5 执行
      e.preventDefault();
      if (!isRunning.value && previewRunnerRef.value) {
        previewRunnerRef.value.run();
      }
      break;
    }
    case 'F8': {
      // F8 继续执行
      e.preventDefault();
      if (isPaused.value) {
        handleContinue();
      }
      break;
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
 * 继续执行
 */
async function handleContinue() {
  if (!debugStore.executionId) return;

  try {
    await resumeExecution(debugStore.executionId);
    debugStore.resumeExecution();
    message.success('继续执行');
  } catch (error: any) {
    message.error(error.message || '继续执行失败');
  }
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

/**
 * 切换检查点启用状态
 */
function handleToggleBreakpoint(nodeId: string) {
  const bp = debugStore.breakpoints.get(nodeId);
  if (bp) {
    debugStore.setBreakpointEnabled(nodeId, !bp.enabled);
  }
}

/**
 * 移除检查点
 */
function handleRemoveBreakpoint(nodeId: string) {
  debugStore.removeBreakpoint(nodeId);
  message.success('已移除检查点');
}

/**
 * 编辑条件检查点
 */
function handleEditCondition(nodeId: string) {
  const bp = debugStore.breakpoints.get(nodeId);
  if (bp) {
    editingBreakpointId.value = nodeId;
    editingCondition.value = bp.condition || '';
    conditionModalVisible.value = true;
  }
}

/**
 * 保存条件检查点
 */
function handleSaveCondition() {
  if (editingBreakpointId.value) {
    debugStore.setBreakpointCondition(
      editingBreakpointId.value,
      editingCondition.value,
    );
    conditionModalVisible.value = false;
    message.success('条件已保存');
  }
}

/**
 * 启用所有检查点
 */
function handleEnableAllBreakpoints() {
  debugStore.enableAllBreakpoints();
  message.success('已启用所有检查点');
}

/**
 * 禁用所有检查点
 */
function handleDisableAllBreakpoints() {
  debugStore.disableAllBreakpoints();
  message.success('已禁用所有检查点');
}

/**
 * 清除所有检查点
 */
function handleClearAllBreakpoints() {
  Modal.confirm({
    title: '确认清除',
    content: '确定要清除所有检查点吗？',
    okText: '确定',
    cancelText: '取消',
    onOk: () => {
      debugStore.clearAllBreakpoints();
      message.success('已清除所有检查点');
    },
  });
}

/**
 * 处理调试变量更新。
 */
async function handleVariableUpdate(
  _nodeId: string,
  variableName: string,
  newValue: any,
) {
  if (!debugStore.executionId) return;

  try {
    await updateVariable(debugStore.executionId, variableName, newValue);
    // 本地更新已由 VariableInspector 处理
    message.success('变量已更新');
  } catch (error: any) {
    message.error(error.message || '更新变量失败');
  }
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
  switchTab: (tab: 'breakpoints' | 'runner' | 'trace' | 'variables') => {
    activeTab.value = tab;
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
        <a-tag v-else-if="isPaused" color="warning" size="small">已暂停</a-tag>
      </div>
      <div class="header-right">
        <!-- 调试控制按钮 -->
        <a-space v-if="isPaused" class="debug-controls">
          <a-tooltip title="继续执行 (F8)">
            <a-button size="small" type="primary" @click="handleContinue">
              <template #icon><FastForwardOutlined /></template>
            </a-button>
          </a-tooltip>
        </a-space>
        <a-divider v-if="isPaused" type="vertical" />
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
        <a-tooltip title="快捷键: F5 执行, F8 继续">
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
            ? { width: `${splitSize}%` }
            : { height: `${splitSize}%` }
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
                  }"
                  @click="handleSelectTrace(trace)"
                >
                  <div class="trace-item-header">
                    <span class="trace-name">{{ trace.nodeName }}</span>
                    <a-tag
                      :color="
                        trace.status === 'completed'
                          ? 'success'
                          : trace.status === 'failed'
                            ? 'error'
                            : trace.status === 'running'
                              ? 'processing'
                              : 'default'
                      "
                      size="small"
                    >
                      {{ trace.status }}
                    </a-tag>
                  </div>
                  <div v-if="trace.duration" class="trace-item-meta">
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
          <a-tab-pane key="breakpoints">
            <template #tab>
              <span>
                检查点
                <a-badge
                  v-if="breakpointCount > 0"
                  :count="breakpointCount"
                  :number-style="{
                    backgroundColor: '#ff4d4f',
                    fontSize: '10px',
                  }"
                />
              </span>
            </template>
            <div class="breakpoint-section">
              <!-- 检查点操作栏 -->
              <div class="breakpoint-toolbar">
                <a-space>
                  <a-button size="small" @click="handleEnableAllBreakpoints">
                    全部启用
                  </a-button>
                  <a-button size="small" @click="handleDisableAllBreakpoints">
                    全部禁用
                  </a-button>
                  <a-button
                    size="small"
                    danger
                    @click="handleClearAllBreakpoints"
                  >
                    清除全部
                  </a-button>
                </a-space>
              </div>
              <!-- 检查点列表 -->
              <div class="breakpoint-list">
                <a-empty
                  v-if="breakpointList.length === 0"
                  description="暂无检查点"
                />
                <div
                  v-for="bp in breakpointList"
                  :key="bp.nodeId"
                  class="breakpoint-item"
                  :class="{ disabled: !bp.enabled }"
                >
                  <a-checkbox
                    :checked="bp.enabled"
                    @change="handleToggleBreakpoint(bp.nodeId)"
                  />
                  <span
                    class="breakpoint-dot"
                    :class="{ active: bp.enabled }"
                  ></span>
                  <span class="breakpoint-name">{{ bp.nodeName }}</span>
                  <a-tag size="small">{{ bp.nodeType }}</a-tag>
                  <span v-if="bp.condition" class="breakpoint-condition">
                    <CodeOutlined />
                  </span>
                  <div class="breakpoint-actions">
                    <a-tooltip title="编辑条件">
                      <a-button
                        type="link"
                        size="small"
                        @click="handleEditCondition(bp.nodeId)"
                      >
                        <SettingOutlined />
                      </a-button>
                    </a-tooltip>
                    <a-tooltip title="删除">
                      <a-button
                        type="link"
                        size="small"
                        danger
                        @click="handleRemoveBreakpoint(bp.nodeId)"
                      >
                        <DeleteOutlined />
                      </a-button>
                    </a-tooltip>
                  </div>
                </div>
              </div>
              <!-- 检查点提示 -->
              <div class="breakpoint-hint">
                <InfoCircleOutlined />
                <span
                  >右键点击节点可添加/移除检查点；当前智能体运行时使用检查点继续执行，不支持旧式单步</span
                >
              </div>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>

      <!-- 分隔条 -->
      <div
        class="panel-splitter"
        :class="{ horizontal: isHorizontalLayout }"
        @mousedown="handleSplitDragStart"
      >
        <div class="splitter-handle"></div>
      </div>

      <!-- 右侧/下方区域: 节点详情 -->
      <div
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

    <!-- 条件检查点编辑弹窗 -->
    <a-modal
      v-model:open="conditionModalVisible"
      title="条件检查点"
      @ok="handleSaveCondition"
    >
      <a-form layout="vertical">
        <a-form-item label="条件表达式">
          <a-input
            v-model:value="editingCondition"
            :placeholder="'例如: {{start.input}} === \'test\''"
          />
          <div class="condition-help">
            当条件为 true 时在节点边界暂停执行，支持变量引用语法。
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
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

// 检查点区域样式
.breakpoint-section {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.breakpoint-toolbar {
  padding: 8px 12px;
  border-bottom: 1px solid var(--ant-color-border-secondary);
}

.breakpoint-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.breakpoint-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 4px;
  background-color: var(--ant-color-error-bg);
  border-radius: 6px;
  transition: opacity 0.2s;

  &.disabled {
    opacity: 0.5;
    background-color: var(--ant-color-bg-layout);
  }

  .breakpoint-dot {
    width: 8px;
    height: 8px;
    background-color: var(--ant-color-text-quaternary);
    border-radius: 50%;
    transition: background-color 0.2s;

    &.active {
      background-color: var(--ant-color-error);
    }
  }

  .breakpoint-name {
    flex: 1;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .breakpoint-condition {
    color: var(--ant-color-warning);
    font-size: 12px;
  }

  .breakpoint-actions {
    display: flex;
    gap: 0;
    opacity: 0;
    transition: opacity 0.2s;
  }

  &:hover .breakpoint-actions {
    opacity: 1;
  }
}

.breakpoint-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--ant-color-text-secondary);
  border-top: 1px solid var(--ant-color-border-secondary);
}

// 条件检查点帮助文本
.condition-help {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ant-color-text-secondary);
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

  .breakpoint-item {
    background-color: rgba(255, 77, 79, 0.1);

    &.disabled {
      background-color: var(--ant-color-bg-elevated);
    }
  }
}
</style>
