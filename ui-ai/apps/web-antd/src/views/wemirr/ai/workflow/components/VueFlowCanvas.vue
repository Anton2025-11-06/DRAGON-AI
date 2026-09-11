<script setup lang="ts">
/**
 * VueFlowCanvas 组件
 * 基于 Vue Flow 的工作流画布组件
 */
import type {
  Connection,
  Edge,
  EdgeChange,
  Node,
  NodeChange,
  NodeMouseEvent,
} from '@vue-flow/core';

import type {
  NodeType,
  WorkflowEdge,
  WorkflowNode as WorkflowNodeType,
} from '#/api/ai-workflow/types';

import { markRaw, onMounted, ref } from 'vue';

import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import {
  ConnectionMode,
  MarkerType,
  useVueFlow,
  VueFlow,
} from '@vue-flow/core';
import { MiniMap } from '@vue-flow/minimap';

import { useAiWorkflowStore } from '#/store/ai-workflow';
import { generateUUID } from '#/utils/uuid';

import { assertWorkflowHandle } from '../domain/ports';
import WorkflowNode from './flow-nodes/WorkflowNode.vue';

// Vue Flow 必需的样式文件
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import '@vue-flow/minimap/dist/style.css';

// ==================== Props ====================

interface Props {
  /** 是否显示小地图 */
  showMinimap?: boolean;
  /** 是否只读模式 */
  readonly?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  showMinimap: true,
  readonly: false,
});

// ==================== Emits ====================

const emit = defineEmits<{
  (e: 'node-click', node: Node): void;
  (e: 'node-dblclick', node: Node): void;
  (e: 'pane-click'): void;
  (e: 'graph-ready'): void;
}>();

// ==================== Store ====================

const workflowStore = useAiWorkflowStore();

// ==================== Vue Flow ====================

const {
  addNodes,
  addEdges,
  removeNodes,
  removeEdges,
  project,
  getNodes,
  getEdges,
  fitView,
  setCenter,
  zoomIn,
  zoomOut,
} = useVueFlow();

// ==================== 状态 ====================

/** 画布容器引用 */
const containerRef = ref<HTMLDivElement | null>(null);

/** 节点列表 */
const nodes = ref<Node[]>([]);

/** 边列表 */
const edges = ref<Edge[]>([]);

/** 节点类型映射 - 使用 any 类型断言解决 Vue Flow 类型兼容问题 */
const nodeTypes: Record<string, any> = {
  workflow: markRaw(WorkflowNode),
};

/** 默认边配置 - 带箭头的平滑连线 */
const defaultEdgeOptions = {
  type: 'smoothstep',
  animated: false,
  markerEnd: {
    type: MarkerType.ArrowClosed,
    color: '#5F95FF',
    width: 20,
    height: 20,
  },
};

// ==================== 事件处理 ====================

/**
 * 节点点击事件
 */
function onNodeClick(event: NodeMouseEvent) {
  const { node } = event;
  if (!node) return;
  workflowStore.selectNode(node.id);
  emit('node-click', node);
}

/**
 * 节点双击事件
 */
function onNodeDoubleClick(event: NodeMouseEvent) {
  const { node } = event;
  if (!node) return;
  emit('node-dblclick', node);
}

/**
 * 面板点击事件（取消选择）
 */
function onPaneClick() {
  workflowStore.clearSelection();
  emit('pane-click');
}

/**
 * 连接事件
 */
function onConnect(connection: Connection) {
  if (props.readonly) return;

  const newEdge: Edge = {
    id: generateUUID(),
    source: connection.source!,
    sourceHandle: connection.sourceHandle || undefined,
    target: connection.target!,
    targetHandle: connection.targetHandle || undefined,
    ...defaultEdgeOptions,
  };

  addEdges([newEdge]);
  workflowStore.setDirty(true);
}

/**
 * 节点变化事件
 */
function onNodesChange(changes: NodeChange[]) {
  if (props.readonly) return;

  // 检测是否有实质性变化
  const hasRealChange = changes.some(
    (change) =>
      change.type === 'position' ||
      change.type === 'remove' ||
      change.type === 'add',
  );

  if (hasRealChange) {
    workflowStore.setDirty(true);
  }
}

/**
 * 边变化事件
 */
function onEdgesChange(changes: EdgeChange[]) {
  if (props.readonly) return;

  const hasRealChange = changes.some(
    (change) => change.type === 'remove' || change.type === 'add',
  );

  if (hasRealChange) {
    workflowStore.setDirty(true);
  }
}

/**
 * 拖放事件 - 从节点面板拖拽节点到画布
 */
function onDrop(event: DragEvent) {
  if (props.readonly) return;

  const nodeType = event.dataTransfer?.getData(
    'application/vueflow-nodetype',
  ) as NodeType;
  if (!nodeType) return;

  // 获取画布容器的边界矩形
  const container = containerRef.value;
  if (!container) return;
  const bounds = container.getBoundingClientRect();

  // 计算相对于画布容器的位置，然后转换为画布坐标
  const position = project({
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top,
  });

  // 创建新节点
  const newNode = createNode(nodeType, position);
  addNodes([newNode]);

  workflowStore.setDirty(true);
}

/**
 * 拖拽悬停事件
 */
function onDragOver(event: DragEvent) {
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'move';
  }
}

// ==================== 节点操作 ====================

/**
 * 创建节点
 */
function createNode(
  nodeType: NodeType,
  position: { x: number; y: number },
): Node {
  const id = generateUUID();
  const activeDefinition = workflowStore.getActiveNodeDefinition(nodeType);
  if (!activeDefinition) {
    throw new Error(`节点定义未加载: ${nodeType}`);
  }
  const label = activeDefinition.displayName;
  const config = { ...activeDefinition.defaultConfig };

  return {
    id,
    type: 'workflow',
    position,
    data: {
      nodeType,
      label,
      config,
      executionStatus: null,
      executionDuration: null,
    },
  };
}

/**
 * 将后端节点数据转换为 Vue Flow 节点
 */
function convertToFlowNodes(workflowNodes: WorkflowNodeType[]): Node[] {
  return workflowNodes.map((node) => ({
    id: node.id,
    type: 'workflow',
    position: { x: node.position.x, y: node.position.y },
    data: {
      nodeType: node.type,
      label: node.label,
      config: node.data || {},
      executionStatus: null,
      executionDuration: null,
    },
  }));
}

/**
 * 将后端边数据转换为 Vue Flow 边
 */
function convertToFlowEdges(workflowEdges: WorkflowEdge[]): Edge[] {
  return workflowEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    sourceHandle: assertWorkflowHandle(edge.sourceHandle),
    target: edge.target,
    targetHandle: assertWorkflowHandle(edge.targetHandle),
    ...defaultEdgeOptions,
  }));
}

/**
 * 将 Vue Flow 节点转换为后端节点数据
 */
function convertToWorkflowNodes(): WorkflowNodeType[] {
  return getNodes.value.map((node) => ({
    id: node.id,
    type: node.data.nodeType as NodeType,
    label: node.data.label || '',
    position: { x: node.position.x, y: node.position.y },
    data: node.data.config || {},
  }));
}

/**
 * 将 Vue Flow 边转换为后端边数据
 */
function convertToWorkflowEdges(): WorkflowEdge[] {
  return getEdges.value.map((edge) => ({
    id: edge.id,
    source: edge.source,
    sourceHandle: assertWorkflowHandle(edge.sourceHandle),
    target: edge.target,
    targetHandle: assertWorkflowHandle(edge.targetHandle),
  }));
}

/**
 * 渲染工作流
 */
function renderWorkflow(
  workflowNodes: WorkflowNodeType[],
  workflowEdges: WorkflowEdge[],
) {
  nodes.value = convertToFlowNodes(workflowNodes);
  edges.value = convertToFlowEdges(workflowEdges);

  // 自适应视图
  setTimeout(() => fitView({ padding: 0.2 }), 100);
}

/**
 * 导出工作流图数据
 */
function exportGraph() {
  return {
    nodes: convertToWorkflowNodes(),
    edges: convertToWorkflowEdges(),
  };
}

/**
 * 清空画布
 */
function clearCanvas() {
  nodes.value = [];
  edges.value = [];
}

/**
 * 更新节点数据
 */
function updateNodeData(nodeId: string, data: Partial<Node['data']>) {
  const node = nodes.value.find((n) => n.id === nodeId);
  if (node) {
    node.data = { ...node.data, ...data };
  }
}

/**
 * 聚焦到指定节点（居中显示）
 */
function focusNode(nodeId: string) {
  const node = nodes.value.find((n) => n.id === nodeId);
  if (node && node.position) {
    // 设置视图中心为节点位置
    setCenter(node.position.x + 100, node.position.y + 50, {
      zoom: 1,
      duration: 300,
    });
  }
}

/**
 * 美化布局：按依赖关系拓扑分层自动排列节点（同层垂直居中），供【适应】按钮一键整理画布
 */
function beautifyLayout() {
  const nodeList = getNodes.value;
  const edgeList = getEdges.value;
  if (nodeList.length === 0) return;

  // 统计入度与后继（自环忽略，避免死循环）
  const indegree = new Map<string, number>();
  const successors = new Map<string, string[]>();
  nodeList.forEach((n) => indegree.set(n.id, 0));
  edgeList.forEach((e) => {
    if (e.source === e.target) return;
    indegree.set(e.target, (indegree.get(e.target) || 0) + 1);
    const list = successors.get(e.source) || [];
    list.push(e.target);
    successors.set(e.source, list);
  });

  // BFS 分层：入度为 0 的节点作为起点逐层展开
  const levelMap = new Map<string, number>();
  const queue = nodeList
    .filter((n) => (indegree.get(n.id) || 0) === 0)
    .map((n) => n.id);
  queue.forEach((id) => levelMap.set(id, 0));
  let level = 0;
  while (queue.length) {
    const size = queue.length;
    for (let i = 0; i < size; i++) {
      const id = queue.shift()!;
      for (const next of successors.get(id) || []) {
        if (!levelMap.has(next)) {
          levelMap.set(next, level + 1);
          queue.push(next);
        }
      }
    }
    level += 1;
  }
  // 环/孤岛兜底：未分层的节点追加到最新层
  const maxLevel = nodeList.reduce(
    (m, n) => Math.max(m, levelMap.get(n.id) ?? 0),
    0,
  );
  nodeList.forEach((n) => {
    if (!levelMap.has(n.id)) levelMap.set(n.id, maxLevel + 1);
  });

  // 收集每层节点，层内按当前 y 排序保持相对顺序，减少跳动
  const layerMap = new Map<number, string[]>();
  nodeList.forEach((n) => {
    const lv = levelMap.get(n.id) ?? 0;
    const arr = layerMap.get(lv) || [];
    arr.push(n.id);
    layerMap.set(lv, arr);
  });
  layerMap.forEach((ids) => {
    ids.sort((a, b) => {
      const na = nodeList.find((x) => x.id === a);
      const nb = nodeList.find((x) => x.id === b);
      return (na?.position?.y ?? 0) - (nb?.position?.y ?? 0);
    });
  });

  // 布局参数：列距/行距/边距，节点尺寸优先取实际渲染尺寸
  const NODE_W = 248;
  const NODE_H = 148;
  const GAP_X = 80;
  const GAP_Y = 56;
  const PAD = 48;

  const levelIds = [...layerMap.keys()].sort((a, b) => a - b);
  const posMap = new Map<string, { x: number; y: number }>();
  let yCursor = PAD;
  levelIds.forEach((lv) => {
    const ids = layerMap.get(lv)!;
    const layerH = ids.length * (NODE_H + GAP_Y) - GAP_Y;
    ids.forEach((id, idx) => {
      const n = nodeList.find((x) => x.id === id);
      if (!n) return;
      const w = n.measured?.width || NODE_W;
      const h = n.measured?.height || NODE_H;
      posMap.set(id, {
        x: PAD + lv * (NODE_W + GAP_X) + (NODE_W - w) / 2,
        y: yCursor + idx * (NODE_H + GAP_Y) + (NODE_H - h) / 2,
      });
    });
    yCursor += layerH + GAP_Y;
  });

  // 写回节点位置（重建数组触发响应式更新）
  nodes.value = nodes.value.map((n) => {
    const p = posMap.get(n.id);
    return p ? { ...n, position: { ...p } } : n;
  });
  workflowStore.setDirty(true);
}

/**
 * 批量设置节点折叠状态
 */
function setAllNodesCollapsed(collapsed: boolean) {
  nodes.value = nodes.value.map((node) => ({
    ...node,
    data: {
      ...node.data,
      collapsed,
    },
  }));
}

// ==================== 生命周期 ====================

onMounted(() => {
  if (import.meta.env.DEV && containerRef.value) {
    (containerRef.value as any).__workflowCanvas = {
      addEdges,
      exportGraph,
      renderWorkflow,
    };
  }
  emit('graph-ready');
});

// ==================== 暴露方法 ====================

defineExpose({
  renderWorkflow,
  exportGraph,
  clearCanvas,
  createNode,
  addNodes,
  removeNodes,
  addEdges,
  removeEdges,
  updateNodeData,
  focusNode,
  beautifyLayout,
  fitView,
  zoomIn,
  zoomOut,
  setAllNodesCollapsed,
  getNodes: () => getNodes.value,
  getEdges: () => getEdges.value,
});
</script>

<template>
  <div
    ref="containerRef"
    class="vue-flow-canvas-container"
    data-testid="workflow-canvas"
  >
    <VueFlow
      v-model:nodes="nodes"
      v-model:edges="edges"
      :node-types="nodeTypes"
      :default-viewport="{ zoom: 1 }"
      :min-zoom="0.3"
      :max-zoom="3"
      :snap-to-grid="true"
      :snap-grid="[10, 10]"
      :connection-mode="ConnectionMode.Loose"
      :delete-key-code="['Backspace', 'Delete']"
      :default-edge-options="defaultEdgeOptions"
      :connect-on-click="false"
      :nodes-draggable="!readonly"
      :nodes-connectable="!readonly"
      :elements-selectable="true"
      fit-view-on-init
      @node-click="onNodeClick"
      @node-double-click="onNodeDoubleClick"
      @pane-click="onPaneClick"
      @connect="onConnect"
      @nodes-change="onNodesChange"
      @edges-change="onEdgesChange"
      @drop="onDrop"
      @dragover="onDragOver"
    >
      <!-- 背景 -->
      <Background pattern-color="#e0e0e0" :gap="10" />

      <!-- 控制面板 -->
      <Controls v-if="!readonly" position="bottom-left" />

      <!-- 小地图 -->
      <MiniMap v-if="showMinimap" position="bottom-right" />

      <!-- 自定义节点模板 -->
      <template #node-workflow="nodeProps">
        <WorkflowNode v-bind="nodeProps" />
      </template>
    </VueFlow>
  </div>
</template>

<style scoped lang="less">
.vue-flow-canvas-container {
  width: 100%;
  height: 100%;
  position: relative;

  :deep(.vue-flow) {
    width: 100%;
    height: 100%;
    background-color: #fafafa;
  }

  :deep(.vue-flow__minimap) {
    background-color: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
  }

  :deep(.vue-flow__controls) {
    background-color: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }

  :deep(.vue-flow__edge-path) {
    stroke: #5f95ff;
    stroke-width: 2;
  }

  // 选中的连接线：绿色加粗显眼（未选中保持蓝色）
  :deep(.vue-flow__edge.selected .vue-flow__edge-path) {
    stroke: #52c41a;
    stroke-width: 4;
  }

  // 箭头颜色跟随连接线状态（覆盖 markerEnd 内联样式）
  :deep(.vue-flow__edge .vue-flow__arrowhead) {
    fill: #5f95ff;
  }

  :deep(.vue-flow__edge.selected .vue-flow__arrowhead) {
    fill: #52c41a !important;
  }

  :deep(.vue-flow__handle) {
    width: 14px;
    height: 14px;
    background-color: #d9d9d9;
    border: 1px solid #bfbfbf;

    &:hover {
      background-color: #1890ff;
      border-color: #1890ff;
    }
  }

  :deep(.vue-flow__handle-connecting) {
    background-color: #1890ff;
    border-color: #1890ff;
  }
}
</style>
