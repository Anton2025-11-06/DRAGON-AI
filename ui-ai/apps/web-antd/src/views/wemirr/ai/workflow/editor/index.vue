<script lang="ts" setup name="WorkflowEditorPage">
/**
 * 工作流编辑器页面
 * 集成画布、节点面板、属性面板、执行监控等组件
 */
import type { NodeType } from '#/api/ai-workflow/types';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';

import {
  ArrowLeftOutlined,
  BugOutlined,
  CheckCircleOutlined,
  ClearOutlined,
  CompressOutlined,
  ExpandOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  SaveOutlined,
  SendOutlined,
  StopOutlined,
  VersionOutlined,
  WarningOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons-vue';
import { Badge, message, Modal, Spin, Tag, Tooltip } from 'ant-design-vue';

import {
  cancelExecution,
  createWorkflow,
  publishWorkflow,
  resumeExecution,
  updateWorkflow,
} from '#/api/ai-workflow';
import { useAiWorkflowStore } from '#/store/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

import DebugPanel from '../components/DebugPanel.vue';
import NodePanel from '../components/NodePanel.vue';
import PropertyPanel from '../components/PropertyPanel.vue';
import VersionHistoryModal from '../components/VersionHistoryModal.vue';
import VueFlowCanvas from '../components/VueFlowCanvas.vue';
import WorkflowDiagnosticsPanel from '../components/WorkflowDiagnosticsPanel.vue';
import { ErrorDetailPanel } from '../components/debug';

const route = useRoute();
const router = useRouter();
const workflowStore = useAiWorkflowStore();
const debugStore = useDebugStore();

// Refs
const canvasRef = ref<InstanceType<typeof VueFlowCanvas>>();
const debugPanelRef = ref<InstanceType<typeof DebugPanel>>();
const loading = ref(false);
const saving = ref(false);
const isFullscreen = ref(false);

// 错误详情面板
const showErrorPanel = ref(false);

// 工作流诊断面板
const showDiagnosticsPanel = ref(false);

// 工作流ID (保持字符串类型，避免 JavaScript 大数精度丢失)
const workflowId = computed(() => {
  const id = route.params.id;
  return id ? String(id) : null;
});

// 是否为新建模式
const isCreateMode = computed(() => !workflowId.value);

// 工作流名称（用于新建时输入）
const workflowName = ref('');
const workflowDescription = ref('');

// 调试面板显示状态
const showDebugPanel = ref(false);

// 调试面板布局模式
const debugPanelLayoutMode = ref<'bottom' | 'right'>('right');

// 发布历史弹窗
const showVersionHistoryModal = ref(false);

// 发布状态与 5 秒点击间隔（防狂点）
const publishing = ref(false);
const publishCountdown = ref(0);
let publishTimer: ReturnType<typeof setInterval> | null = null;

// 是否有未保存的更改
const isDirty = computed(() => workflowStore.isDirty);

// 当前工作流
const currentWorkflow = computed(() => workflowStore.currentWorkflow);

// 调试状态计算属性
const isDebugRunning = computed(() => debugStore.isRunning);
const isDebugPaused = computed(() => debugStore.isPaused);
const hasCurrentError = computed(() => debugStore.hasCurrentError);
const currentError = computed(() => debugStore.currentError);
const breakpointCount = computed(() => debugStore.breakpointCount);
const diagnostics = computed(() => {
  void workflowStore.graphRevision;
  return workflowStore.validateWorkflowGraph(workflowStore.exportGraph());
});
const diagnosticIssueCount = computed(() => diagnostics.value.issues.length);
const diagnosticBlockingCount = computed(
  () =>
    diagnostics.value.issues.filter((issue) => issue.severity === 'ERROR')
      .length,
);

// 从 START 节点获取输入变量定义
// 优先从画布读取（实时数据），其次从 currentWorkflow 读取（已保存数据）
const inputVariables = computed(() => {
  // 优先从画布获取实时数据
  const nodes = canvasRef.value?.getNodes() || [];
  const startNode = nodes.find((node: any) => node.data?.nodeType === 'START');
  if (startNode) {
    const fields = startNode.data?.config?.fields || [];
    if (fields.length > 0) {
      return fields.map((field: any) => ({
        name: field.name,
        label: field.label || field.name,
        type: field.type || 'SHORT_TEXT',
        defaultValue: field.defaultValue,
        description: field.description || field.label,
        required: field.required || false,
        options: field.options,
        maxLength: field.maxLength,
        minValue: field.minValue,
        maxValue: field.maxValue,
        allowedFileTypes: field.allowedFileTypes,
        maxFileSize: field.maxFileSize,
        maxFileCount: field.maxFileCount,
        pattern: field.pattern,
        patternMessage: field.patternMessage,
      }));
    }
  }

  // 从已保存的工作流数据读取 START 字段
  const graph = currentWorkflow.value?.graph;
  if (!graph?.nodes) return [];

  // 找到 START 节点
  const savedStartNode = graph.nodes.find((node: any) => node.type === 'START');
  // 配置可能在 data.config.fields 或 data.fields 中
  const fields =
    savedStartNode?.data?.config?.fields || savedStartNode?.data?.fields || [];
  if (fields.length === 0) return [];

  // 将 START 节点的 fields 转换为 InputField 格式
  return fields.map((field: any) => ({
    name: field.name,
    label: field.label || field.name,
    type: field.type || 'SHORT_TEXT',
    defaultValue: field.defaultValue,
    description: field.description || field.label,
    required: field.required || false,
    options: field.options,
    maxLength: field.maxLength,
    minValue: field.minValue,
    maxValue: field.maxValue,
    allowedFileTypes: field.allowedFileTypes,
    maxFileSize: field.maxFileSize,
    maxFileCount: field.maxFileCount,
    pattern: field.pattern,
    patternMessage: field.patternMessage,
  }));
});

/**
 * 根据节点ID获取节点的友好名称
 * 用于在调试面板中显示友好的节点名称而不是 UUID
 */
function getNodeFriendlyName(nodeId: string): string {
  const nodes = canvasRef.value?.getNodes() || [];
  const node = nodes.find((n: any) => n.id === nodeId);
  if (!node) return nodeId;

  return node.data?.label || nodeId;
}

// 状态配置
const statusConfig: Record<string, { color: string; text: string }> = {
  DRAFT: { color: 'default', text: '草稿' },
  PUBLISHED: { color: 'success', text: '已发布' },
};

/**
 * 加载工作流
 */
async function loadWorkflow() {
  if (!workflowId.value) return;

  loading.value = true;
  try {
    await workflowStore.loadWorkflow(workflowId.value);
    workflowName.value = workflowStore.currentWorkflow?.name || '';
    workflowDescription.value =
      workflowStore.currentWorkflow?.description || '';
    openDebugPanelFromRoute();
  } catch {
    message.error('加载工作流失败');
  } finally {
    loading.value = false;
  }
}

function shouldOpenDebugFromRoute() {
  const execute = route.query.execute;
  return execute === 'true' || execute === '1';
}

function openDebugPanelFromRoute() {
  if (!workflowId.value || !shouldOpenDebugFromRoute()) return;

  workflowStore.isDebugMode = true;
  showDebugPanel.value = true;
  debugStore.setNodeNameResolver(getNodeFriendlyName);
}

/**
 * 保存工作流
 */
async function handleSave() {
  if (!workflowName.value.trim()) {
    message.warning('请输入工作流名称');
    return;
  }

  saving.value = true;
  try {
    const graphData = workflowStore.exportGraph();
    const validation = workflowStore.validateWorkflowGraph(graphData);
    if (!validation.valid) {
      showDiagnosticsPanel.value = true;
      message.error('工作流诊断存在阻断问题，请先修复');
      return;
    }

    if (isCreateMode.value) {
      // 创建新工作流
      const id = await createWorkflow({
        name: workflowName.value.trim(),
        description: workflowDescription.value,
        graph: graphData,
      });
      message.success('创建成功');
      // 跳转到编辑页面
      router.replace(`/agent/workflow/editor/${id}`);
    } else {
      // 更新工作流
      await updateWorkflow(workflowId.value!, {
        name: workflowName.value.trim(),
        description: workflowDescription.value,
        graph: graphData,
      });
      message.success('保存成功');
      workflowStore.isDirty = false;
    }
  } catch {
    message.error(isCreateMode.value ? '创建失败' : '保存失败');
  } finally {
    saving.value = false;
  }
}

/**
 * 返回列表
 */
function handleBack() {
  if (isDirty.value) {
    Modal.confirm({
      title: '确认离开',
      content: '有未保存的更改，确定要离开吗？',
      okText: '离开',
      cancelText: '取消',
      onOk: () => {
        router.push('/agent/workflow');
      },
    });
  } else {
    router.push('/agent/workflow');
  }
}

/**
 * 处理节点拖拽开始
 * Vue Flow 使用 HTML5 拖放 API
 */
function handleNodeDragStart(nodeType: NodeType, event: DragEvent) {
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow-nodetype', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  }
}

/**
 * 处理画布就绪
 */
async function handleGraphReady() {
  // 设置画布引用到 Store
  if (canvasRef.value) {
    workflowStore.setCanvasRef(canvasRef.value);
  }
  if (!workflowStore.nodeDefinitionsLoaded) {
    await workflowStore.loadNodeDefinitions().catch(() => undefined);
  }

  // 标记画布已就绪
  canvasReady.value = true;

  // 处理待加载的工作流
  if (pendingWorkflowId.value) {
    await loadWorkflow();
    pendingWorkflowId.value = null;
    openDebugPanelFromRoute();
  } else if (isCreateMode.value) {
    // 新建模式，创建空工作流
    workflowStore.createNewWorkflow('新建工作流');
  }
}

/**
 * 处理节点点击
 */
function handleNodeClick(_node: any) {
  // 节点选中由 VueFlowCanvas 和 store 处理
}

/**
 * 处理空白区域点击
 */
function handleBlankClick() {
  // 取消选中由 store 处理
}

/**
 * 处理删除节点
 */
function handleDeleteNode(nodeId: string) {
  canvasRef.value?.removeNodes([nodeId]);
}

/**
 * 处理更新节点配置
 */
function handleUpdateConfig(nodeId: string, config: Record<string, any>) {
  workflowStore.updateNodeConfig(nodeId, config);
}

/**
 * 处理更新节点标签
 */
function handleUpdateLabel(nodeId: string, label: string) {
  workflowStore.updateNodeLabel(nodeId, label);
}

/**
 * 放大
 */
function handleZoomIn() {
  canvasRef.value?.zoomIn();
}

/**
 * 缩小
 */
function handleZoomOut() {
  canvasRef.value?.zoomOut();
}

/**
 * 适应画布
 */
function handleZoomToFit() {
  canvasRef.value?.fitView();
}

/**
 * 切换全屏
 */
function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value;
}

/**
 * 折叠所有节点
 */
function handleCollapseAll() {
  canvasRef.value?.setAllNodesCollapsed(true);
}

/**
 * 展开所有节点
 */
function handleExpandAll() {
  canvasRef.value?.setAllNodesCollapsed(false);
}

/**
 * 切换调试模式
 */
function toggleDebugMode() {
  workflowStore.isDebugMode = !workflowStore.isDebugMode;
  showDebugPanel.value = workflowStore.isDebugMode;

  // 如果开启调试模式，设置节点名称解析器
  if (workflowStore.isDebugMode) {
    debugStore.setNodeNameResolver(getNodeFriendlyName);
  }

  // 如果关闭调试模式，重置调试状态
  if (!workflowStore.isDebugMode) {
    debugStore.clearExecutionState();
  }
}

/**
 * 停止调试执行
 */
async function handleStopDebug() {
  if (!debugStore.executionId) return;

  try {
    await cancelExecution(debugStore.executionId);
    debugStore.cancelExecution();
    message.success('已停止执行');
  } catch (error: any) {
    message.error(error.message || '停止执行失败');
  }
}

/**
 * 继续执行 (从断点)
 */
async function handleContinueDebug() {
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
 * 清除所有断点
 */
function handleClearBreakpoints() {
  Modal.confirm({
    title: '确认清除',
    content: '确定要清除所有断点吗？',
    okText: '确定',
    cancelText: '取消',
    onOk: () => {
      debugStore.clearAllBreakpoints();
      message.success('已清除所有断点');
    },
  });
}

/**
 * 显示错误详情面板
 */
function handleShowErrorPanel() {
  showErrorPanel.value = true;
}

/**
 * 处理错误面板中的节点跳转
 */
function handleErrorNodeClick(nodeId: string) {
  handleDebugNodeClick(nodeId);
  showErrorPanel.value = false;
}

/**
 * 处理调试面板布局变化。
 */
function handleDebugLayoutChange(mode: 'bottom' | 'right') {
  debugPanelLayoutMode.value = mode;
}

/**
 * 处理调试面板节点点击
 */
function handleDebugNodeClick(nodeId: string) {
  // 居中并选中节点
  canvasRef.value?.focusNode(nodeId);
  // 更新 store 中的选中节点
  workflowStore.selectNode(nodeId);
}

/**
 * 发布工作流（保存右侧，5 秒点击间隔防狂点）
 */
function handlePublish() {
  if (!workflowId.value) {
    message.warning('请先保存工作流');
    return;
  }
  Modal.confirm({
    title: '确认发布',
    content: '发布后生成版本快照，正式执行与第三方 API 将使用最新版本。',
    okText: '发布',
    cancelText: '取消',
    onOk: async () => {
      publishing.value = true;
      try {
        await publishWorkflow(workflowId.value!);
        message.success('发布成功');
        await loadWorkflow(); // 刷新状态（PUBLISHED 与版本号）
        startPublishCountdown();
      } catch {
        message.error('发布失败，请先修复工作流诊断中的阻断问题');
      } finally {
        publishing.value = false;
      }
    },
  });
}

/** 发布成功后进入 5 秒冷却，防止连续发布 */
function startPublishCountdown() {
  publishCountdown.value = 5;
  if (publishTimer) clearInterval(publishTimer);
  publishTimer = setInterval(() => {
    publishCountdown.value -= 1;
    if (publishCountdown.value <= 0 && publishTimer) {
      clearInterval(publishTimer);
      publishTimer = null;
    }
  }, 1000);
}

/**
 * 发布历史：恢复版本成功后重新加载，页面显示该版本的工作流
 */
async function handleVersionRestored() {
  await loadWorkflow();
  message.success('已切换到恢复后的版本');
}

// 画布是否已就绪
const canvasReady = ref(false);

// 待加载的工作流ID（画布就绪后加载）
const pendingWorkflowId = ref<string | null>(null);

// 监听路由参数变化
watch(
  () => route.params.id,
  async (newId, oldId) => {
    // 路由变化时，先重置状态（避免旧数据残留）
    if (oldId !== undefined) {
      workflowStore.resetEditorSession();
      if (canvasRef.value) {
        workflowStore.setCanvasRef(canvasRef.value);
      }
    }

    // 清空本地状态
    workflowName.value = '';
    workflowDescription.value = '';
    showDebugPanel.value = false;

    if (newId) {
      // 编辑模式：如果画布已就绪，直接加载；否则标记待加载
      if (canvasReady.value) {
        await loadWorkflow();
        openDebugPanelFromRoute();
      } else {
        pendingWorkflowId.value = String(newId);
      }
    } else {
      // 新建模式：如果画布已就绪，创建新工作流
      if (canvasReady.value) {
        workflowStore.createNewWorkflow('新建工作流');
      }
      workflowName.value = '新建工作流';
      workflowDescription.value = '';
    }
  },
  { immediate: true },
);

onMounted(() => {
  if (!import.meta.env.DEV) return;
  (window as any).__workflowEditor = {
    debug: {
      getExecutionResult: () =>
        debugPanelRef.value?.getExecutionResult() || null,
      getInputValues: () => debugPanelRef.value?.getRunnerInputValues() || {},
      getNodeTraces: () => debugPanelRef.value?.getNodeTraces() || [],
      runPreview: () => debugPanelRef.value?.runPreview(),
      setInputValues: async (values: Record<string, any>) => {
        if (!showDebugPanel.value) {
          workflowStore.isDebugMode = true;
          showDebugPanel.value = true;
          debugStore.setNodeNameResolver(getNodeFriendlyName);
          await nextTick();
        }
        debugPanelRef.value?.setRunnerInputValues(values);
      },
    },
    getState: () => ({
      canvasNodeCount: canvasRef.value?.getNodes()?.length || 0,
      edgeCount: canvasRef.value?.getEdges()?.length || 0,
      isDebugMode: workflowStore.isDebugMode,
      selectedNode: workflowStore.selectedNode,
      showDebugPanel: showDebugPanel.value,
      workflowDescription: workflowDescription.value,
      workflowId: workflowId.value,
      workflowName: workflowName.value,
    }),
    selectFirstNonBoundaryNode: () => {
      const node =
        canvasRef.value
          ?.getNodes()
          ?.find(
            (item: any) => !['START', 'END'].includes(item.data?.nodeType),
          ) || canvasRef.value?.getNodes()?.[0];
      if (node) {
        workflowStore.selectNode(node.id);
        return {
          id: node.id,
          type: node.data?.nodeType,
        };
      }
      return null;
    },
  };
});

// 页面离开前提示
onBeforeUnmount(() => {
  if (publishTimer) {
    clearInterval(publishTimer);
    publishTimer = null;
  }
  if (import.meta.env.DEV && (window as any).__workflowEditor) {
    delete (window as any).__workflowEditor;
  }
  workflowStore.$reset();
});
</script>

<template>
  <div class="workflow-editor" :class="{ fullscreen: isFullscreen }">
    <Spin
      :spinning="loading"
      tip="加载中..."
      wrapper-class-name="workflow-spin-wrapper"
    >
      <!-- 顶部工具栏 -->
      <div class="editor-header">
        <div class="header-left">
          <a-button type="text" @click="handleBack">
            <template #icon><ArrowLeftOutlined /></template>
          </a-button>
          <a-input
            v-model:value="workflowName"
            class="workflow-name-input"
            placeholder="请输入工作流名称"
            :bordered="false"
          />
          <Tag
            v-if="currentWorkflow?.status"
            :color="statusConfig[currentWorkflow.status]?.color"
          >
            {{ statusConfig[currentWorkflow.status]?.text }}
          </Tag>
          <span v-if="isDirty" class="dirty-indicator">*</span>
        </div>

        <div class="header-center">
          <a-space>
            <a-tooltip title="放大">
              <a-button type="text" @click="handleZoomIn">
                <template #icon><ZoomInOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="缩小">
              <a-button type="text" @click="handleZoomOut">
                <template #icon><ZoomOutOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="适应画布">
              <a-button type="text" @click="handleZoomToFit"> 适应 </a-button>
            </a-tooltip>
            <a-divider type="vertical" />
            <a-tooltip title="折叠所有节点">
              <a-button type="text" @click="handleCollapseAll">
                <template #icon><CompressOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="展开所有节点">
              <a-button type="text" @click="handleExpandAll">
                <template #icon><ExpandOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-divider type="vertical" />
            <a-tooltip :title="isFullscreen ? '退出全屏' : '全屏'">
              <a-button type="text" @click="toggleFullscreen">
                <template #icon>
                  <FullscreenExitOutlined v-if="isFullscreen" />
                  <FullscreenOutlined v-else />
                </template>
              </a-button>
            </a-tooltip>
          </a-space>
        </div>

        <div class="header-right">
          <a-space>
            <!-- 调试控制按钮组 -->
            <template v-if="workflowStore.isDebugMode">
              <!-- 断点计数 -->
              <Tooltip v-if="breakpointCount > 0" title="断点数量">
                <Badge
                  :count="breakpointCount"
                  :number-style="{ backgroundColor: '#ff4d4f' }"
                >
                  <a-button type="text" @click="handleClearBreakpoints">
                    <template #icon><ClearOutlined /></template>
                  </a-button>
                </Badge>
              </Tooltip>

              <!-- 错误提示 -->
              <Tooltip v-if="hasCurrentError" title="查看错误详情">
                <a-button type="text" danger @click="handleShowErrorPanel">
                  <template #icon><WarningOutlined /></template>
                </a-button>
              </Tooltip>

              <!-- 调试执行控制 -->
              <template v-if="isDebugRunning">
                <Tooltip title="停止执行">
                  <a-button type="text" danger @click="handleStopDebug">
                    <template #icon><StopOutlined /></template>
                  </a-button>
                </Tooltip>
              </template>

              <template v-if="isDebugPaused">
                <Tooltip title="继续执行 (F8)">
                  <a-button type="primary" ghost @click="handleContinueDebug">
                    <template #icon><PlayCircleOutlined /></template>
                  </a-button>
                </Tooltip>
              </template>

              <a-divider type="vertical" />
            </template>

            <a-tooltip title="调试模式">
              <a-button
                :type="workflowStore.isDebugMode ? 'primary' : 'default'"
                @click="toggleDebugMode"
              >
                <template #icon><BugOutlined /></template>
                调试
                <Tag
                  v-if="isDebugRunning"
                  color="processing"
                  size="small"
                  style="margin-left: 4px"
                  >运行中</Tag
                >
                <Tag
                  v-else-if="isDebugPaused"
                  color="warning"
                  size="small"
                  style="margin-left: 4px"
                  >已暂停</Tag
                >
              </a-button>
            </a-tooltip>
            <a-tooltip title="工作流诊断">
              <Badge
                :count="diagnosticIssueCount"
                :number-style="{
                  backgroundColor:
                    diagnosticBlockingCount > 0 ? '#ff4d4f' : '#faad14',
                }"
              >
                <a-button
                  :danger="diagnosticBlockingCount > 0"
                  @click="showDiagnosticsPanel = true"
                >
                  <template #icon>
                    <WarningOutlined v-if="diagnosticBlockingCount > 0" />
                    <CheckCircleOutlined v-else />
                  </template>
                  诊断
                </a-button>
              </Badge>
            </a-tooltip>
            <a-tooltip title="发布历史（版本查看与恢复）">
              <a-button
                :disabled="!workflowId"
                @click="showVersionHistoryModal = true"
              >
                <template #icon><VersionOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-button type="primary" :loading="saving" @click="handleSave">
              <template #icon><SaveOutlined /></template>
              保存
            </a-button>
            <a-button
              type="primary"
              ghost
              :loading="publishing"
              :disabled="isCreateMode || publishCountdown > 0"
              @click="handlePublish"
            >
              <template #icon><SendOutlined /></template>
              {{ publishCountdown > 0 ? `发布 (${publishCountdown}s)` : '发布' }}
            </a-button>
          </a-space>
        </div>
      </div>

      <!-- 主体区域 -->
      <div class="editor-body">
        <!-- 左侧节点面板 -->
        <div class="left-panel">
          <NodePanel @drag-start="handleNodeDragStart" />
        </div>

        <!-- 中间画布区域 -->
        <div class="center-panel">
          <VueFlowCanvas
            ref="canvasRef"
            :show-minimap="true"
            :readonly="false"
            @graph-ready="handleGraphReady"
            @node-click="handleNodeClick"
            @pane-click="handleBlankClick"
          />
        </div>

        <!-- 右侧属性面板 -->
        <div class="right-panel">
          <PropertyPanel
            :selected-node="selectedNode"
            :show-trace="workflowStore.isDebugMode"
            @delete-node="handleDeleteNode"
            @update-config="handleUpdateConfig"
            @update-label="handleUpdateLabel"
            @node-click="handleDebugNodeClick"
          />
        </div>
      </div>

      <!-- 调试面板 - 右侧抽屉模式 -->
      <a-drawer
        v-if="debugPanelLayoutMode === 'right'"
        v-model:open="showDebugPanel"
        title="调试面板"
        placement="right"
        :width="720"
        :mask="false"
      >
        <DebugPanel
          ref="debugPanelRef"
          :workflow-id="workflowId || ''"
          :input-fields="inputVariables"
          :layout-mode="debugPanelLayoutMode"
          @layout-change="handleDebugLayoutChange"
          @node-click="handleDebugNodeClick"
        />
      </a-drawer>

      <!-- 调试面板 - 底部抽屉模式 -->
      <a-drawer
        v-else
        v-model:open="showDebugPanel"
        title="调试面板"
        placement="bottom"
        :height="560"
        :mask="false"
      >
        <DebugPanel
          ref="debugPanelRef"
          :workflow-id="workflowId || ''"
          :input-fields="inputVariables"
          :layout-mode="debugPanelLayoutMode"
          @layout-change="handleDebugLayoutChange"
          @node-click="handleDebugNodeClick"
        />
      </a-drawer>

      <!-- 错误详情面板 -->
      <a-drawer
        v-model:open="showErrorPanel"
        title="错误详情"
        placement="right"
        :width="480"
      >
        <ErrorDetailPanel
          v-if="currentError"
          :error="currentError"
          @navigate-to-node="handleErrorNodeClick"
          @close="showErrorPanel = false"
        />
      </a-drawer>

      <!-- 发布历史弹窗 -->
      <VersionHistoryModal
        v-model:open="showVersionHistoryModal"
        :workflow-id="workflowId || ''"
        :current-version="currentWorkflow?.currentVersion"
        @restored="handleVersionRestored"
      />

      <!-- 工作流诊断抽屉 -->
      <a-drawer
        v-model:open="showDiagnosticsPanel"
        title="工作流诊断"
        placement="right"
        :width="560"
      >
        <WorkflowDiagnosticsPanel :issues="diagnostics.issues" />
      </a-drawer>

      <!-- 保存为模板功能已迁移至工作流列表页（需求 4.2） -->
    </Spin>
  </div>
</template>

<style lang="less" scoped>
.workflow-editor {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
  background-color: #f5f5f5;

  &.fullscreen {
    position: fixed;
    top: 0;
    left: 0;
    z-index: 1000;
  }

  // 确保 Spin 组件不影响布局
  :deep(.workflow-spin-wrapper) {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;

    .ant-spin-container {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
    }
  }
}

.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 16px;
  background-color: #fff;
  border-bottom: 1px solid #e8e8e8;

  .header-left {
    display: flex;
    gap: 8px;
    align-items: center;

    .workflow-name-input {
      width: 200px;
      font-size: 16px;
      font-weight: 500;

      &:hover,
      &:focus {
        background-color: #f5f5f5;
      }
    }

    .dirty-indicator {
      font-size: 18px;
      color: #ff4d4f;
    }
  }

  .header-center {
    display: flex;
    align-items: center;
  }

  .header-right {
    display: flex;
    align-items: center;
  }
}

.editor-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.left-panel {
  flex: 0 0 260px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: #fff;
  border-right: 1px solid #e8e8e8;
}

.center-panel {
  flex: 1;
  min-width: 0;
  position: relative;
  overflow: hidden;
}

.right-panel {
  flex: 0 0 460px;
  display: flex;
  flex-direction: column;
  min-width: 380px;
  max-width: min(520px, 36vw);
  overflow: hidden;
  background-color: #fff;
  border-left: 1px solid #e8e8e8;
}
</style>
