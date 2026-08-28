<script lang="ts" setup>
/**
 * 知识图谱可视化弹窗组件
 * 使用AntV G6进行图谱可视化展示
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

import {
  ApartmentOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Card,
  Empty,
  InputNumber,
  message,
  Modal,
  Space,
  Spin,
  Statistic,
} from 'ant-design-vue';

import type { GraphVisualizationResp } from './api';
import { GetVisualizationData } from './api';

const props = defineProps<{
  open: boolean;
  knowledgeBaseId: number | null;
  knowledgeBaseName?: string;
}>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
}>();

const loading = ref(false);
const graphData = ref<GraphVisualizationResp | null>(null);
const graphContainer = ref<HTMLDivElement | null>(null);
const nodeLimit = ref(100);
const isFullscreen = ref(false);
const zoomLevel = ref(1);

// G6实例
let graph: any = null;

// 优先实体类型（按优先级排序，使用大写用于匹配）
const priorityEntityTypes = ['PERSON', 'ORGANIZATION', 'LOCATION', 'EVENT', 'CONCEPT'];

// 节点颜色映射（使用大写key）
const colorMap: Record<string, string> = {
  PERSON: '#5B8FF9',
  ORGANIZATION: '#5AD8A6',
  LOCATION: '#5D7092',
  EVENT: '#F6BD16',
  CONCEPT: '#E8684A',
  DOCUMENT: '#6DC8EC',
  ENTITY: '#9270CA',
  DEFAULT: '#269A99',
};

// 图例显示映射（用于前端展示）
const legendMap: Record<string, { label: string; color: string }> = {
  Person: { label: 'Person (人物)', color: '#5B8FF9' },
  Organization: { label: 'Organization (组织)', color: '#5AD8A6' },
  Location: { label: 'Location (地点)', color: '#5D7092' },
  Event: { label: 'Event (事件)', color: '#F6BD16' },
  Concept: { label: 'Concept (概念)', color: '#E8684A' },
  Document: { label: 'Document (文档)', color: '#6DC8EC' },
  Default: { label: '默认', color: '#269A99' },
};

/**
 * 根据节点的labels数组获取显示颜色
 * 优先按照 Person、Organization、Location、Event、Concept 顺序匹配
 * 大小写不敏感
 */
const getNodeColorByLabels = (labels: string[]): string => {
  if (!labels || labels.length === 0) {
    return colorMap.DEFAULT || '#269A99';
  }
  // 转换为大写进行匹配
  const upperLabels = labels.map(l => l.toUpperCase());
  // 按优先级查找匹配的实体类型
  for (const entityType of priorityEntityTypes) {
    if (upperLabels.includes(entityType)) {
      return colorMap[entityType] || colorMap.DEFAULT || '#269A99';
    }
  }
  // 没有匹配优先实体类型，尝试匹配其他已定义颜色
  for (const label of upperLabels) {
    if (colorMap[label]) {
      return colorMap[label];
    }
  }
  return colorMap.DEFAULT || '#269A99';
};

/**
 * 根据节点的labels数组获取主要显示类型
 * 用于在节点上显示主要实体类型
 * 大小写不敏感
 */
const getPrimaryLabel = (labels: string[]): string => {
  if (!labels || labels.length === 0) {
    return 'Entity';
  }
  // 转换为大写进行匹配
  const upperLabels = labels.map(l => l.toUpperCase());
  // 按优先级查找匹配的实体类型
  for (const entityType of priorityEntityTypes) {
    const idx = upperLabels.indexOf(entityType);
    if (idx !== -1 && labels[idx]) {
      return labels[idx];
    }
  }
  // 返回第一个非通用标签（过滤掉KB_开头的标签）
  const filtered = labels.filter(l => 
    l.toUpperCase() !== 'ENTITY' && 
    l.toUpperCase() !== '__ENTITY__' &&
    !l.startsWith('KB_')
  );
  return filtered[0] || labels[0] || 'Entity';
};

// 加载图谱数据
const loadGraphData = async () => {
  if (!props.knowledgeBaseId) return;

  loading.value = true;
  try {
    const data = await GetVisualizationData(props.knowledgeBaseId, nodeLimit.value);
    graphData.value = data;
    await nextTick();
    renderGraph();
  } catch (error) {
    console.error('加载图谱数据失败:', error);
    message.error('加载图谱数据失败');
  } finally {
    loading.value = false;
  }
};

// 渲染图谱
const renderGraph = async () => {
  if (!graphData.value || !graphContainer.value) return;

  // 清理旧实例
  if (graph) {
    graph.destroy();
    graph = null;
  }

  // 动态导入G6
  const G6 = await import('@antv/g6');

  const { nodes, edges } = graphData.value;

  if (nodes.length === 0) {
    return;
  }

  // 转换数据格式
  const g6Data = {
    nodes: nodes.map((node) => {
      const labels = node.labels || [node.label];
      const primaryLabel = getPrimaryLabel(labels);
      const nodeColor = getNodeColorByLabels(labels);
      return {
        id: node.id,
        label: node.name || node.label,
        nodeType: primaryLabel,
        allLabels: labels, // 保存所有标签用于详情展示
        properties: node.properties,
        style: {
          fill: nodeColor,
          stroke: nodeColor,
          lineWidth: 2,
        },
      };
    }),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label || edge.type,
      properties: edge.properties,
    })),
  };

  const container = graphContainer.value;
  const width = container.offsetWidth;
  const height = container.offsetHeight;

  graph = new G6.Graph({
    container,
    width,
    height,
    fitView: true,
    fitViewPadding: 20,
    animate: true,
    modes: {
      default: ['drag-canvas', 'zoom-canvas', 'drag-node', 'click-select'],
    },
    layout: {
      type: 'force',
      preventOverlap: true,
      nodeSize: 60,
      linkDistance: 150,
      nodeStrength: -300,
      edgeStrength: 0.1,
      collideStrength: 0.8,
    },
    defaultNode: {
      type: 'circle',
      size: 40,
      labelCfg: {
        position: 'bottom',
        offset: 5,
        style: {
          fontSize: 12,
          fill: '#333',
        },
      },
      style: {
        lineWidth: 2,
      },
    },
    defaultEdge: {
      type: 'quadratic',
      style: {
        stroke: '#e2e2e2',
        lineWidth: 1.5,
        endArrow: {
          path: G6.Arrow.triangle(6, 8, 0),
          fill: '#e2e2e2',
        },
      },
      labelCfg: {
        autoRotate: true,
        style: {
          fontSize: 10,
          fill: '#999',
          background: {
            fill: '#fff',
            padding: [2, 4, 2, 4],
            radius: 2,
          },
        },
      },
    },
    nodeStateStyles: {
      selected: {
        stroke: '#1890ff',
        lineWidth: 3,
        shadowColor: '#1890ff',
        shadowBlur: 10,
      },
      hover: {
        stroke: '#1890ff',
        lineWidth: 2,
      },
    },
    edgeStateStyles: {
      selected: {
        stroke: '#1890ff',
        lineWidth: 2,
      },
    },
  });

  graph.data(g6Data);
  graph.render();

  // 添加节点悬浮效果
  graph.on('node:mouseenter', (e: any) => {
    graph.setItemState(e.item, 'hover', true);
  });

  graph.on('node:mouseleave', (e: any) => {
    graph.setItemState(e.item, 'hover', false);
  });

  // 节点点击显示详情
  graph.on('node:click', (e: any) => {
    const model = e.item.getModel();
    // 显示所有标签
    const allLabels = model.allLabels || [model.nodeType];
    const labelsText = allLabels.join(', ');
    Modal.info({
      title: `节点详情: ${model.label}`,
      content: `标签: ${labelsText}`,
      width: 400,
    });
  });
};

// 缩放操作
const handleZoomIn = () => {
  if (graph) {
    zoomLevel.value = Math.min(zoomLevel.value + 0.2, 3);
    graph.zoomTo(zoomLevel.value);
  }
};

const handleZoomOut = () => {
  if (graph) {
    zoomLevel.value = Math.max(zoomLevel.value - 0.2, 0.2);
    graph.zoomTo(zoomLevel.value);
  }
};

// 适应画布
const handleFitView = () => {
  if (graph) {
    graph.fitView(20);
    zoomLevel.value = graph.getZoom();
  }
};

// 刷新图谱
const handleRefresh = () => {
  loadGraphData();
};

// 切换全屏
const toggleFullscreen = () => {
  isFullscreen.value = !isFullscreen.value;
  nextTick(() => {
    if (graph && graphContainer.value) {
      graph.changeSize(
        graphContainer.value.offsetWidth,
        graphContainer.value.offsetHeight,
      );
      graph.fitView(20);
    }
  });
};

// 监听弹窗打开
watch(
  () => props.open,
  (val) => {
    if (val && props.knowledgeBaseId) {
      loadGraphData();
    }
  },
);

// 监听容器大小变化
const resizeObserver = ref<ResizeObserver | null>(null);

watch(graphContainer, (container) => {
  if (container) {
    resizeObserver.value = new ResizeObserver(() => {
      if (graph) {
        graph.changeSize(container.offsetWidth, container.offsetHeight);
      }
    });
    resizeObserver.value.observe(container);
  }
});

onBeforeUnmount(() => {
  if (graph) {
    graph.destroy();
    graph = null;
  }
  if (resizeObserver.value) {
    resizeObserver.value.disconnect();
  }
});

// 计算弹窗样式
const modalStyle = computed(() => ({
  top: isFullscreen.value ? '0' : '20px',
}));

const modalBodyStyle = computed(() => ({
  height: isFullscreen.value ? 'calc(100vh - 55px)' : '70vh',
  padding: '12px',
  overflow: 'hidden',
}));
</script>

<template>
  <Modal
    :open="open"
    :title="null"
    :width="isFullscreen ? '100%' : '90%'"
    :style="modalStyle"
    :bodyStyle="modalBodyStyle"
    :footer="null"
    :destroyOnClose="true"
    @cancel="emit('update:open', false)"
  >
    <template #title>
      <div class="flex items-center justify-between pr-8">
        <div class="flex items-center">
          <ApartmentOutlined class="mr-2 text-blue-500" />
          <span>{{ knowledgeBaseName || '知识库' }} - 知识图谱</span>
        </div>
        <Space>
          <span class="text-sm text-gray-500">节点数量限制:</span>
          <InputNumber
            v-model:value="nodeLimit"
            :min="10"
            :max="500"
            :step="10"
            size="small"
            style="width: 80px"
            @change="handleRefresh"
          />
        </Space>
      </div>
    </template>

    <div class="graph-wrapper flex h-full flex-col">
      <!-- 统计信息 -->
      <div class="mb-3 flex items-center justify-between">
        <Space :size="24">
          <Statistic
            title="节点数"
            :value="graphData?.nodeCount || 0"
            :valueStyle="{ fontSize: '16px', color: '#1890ff' }"
          />
          <Statistic
            title="关系数"
            :value="graphData?.edgeCount || 0"
            :valueStyle="{ fontSize: '16px', color: '#52c41a' }"
          />
        </Space>

        <!-- 工具栏 -->
        <Space>
          <Button size="small" @click="handleZoomIn">
            <template #icon><ZoomInOutlined /></template>
          </Button>
          <Button size="small" @click="handleZoomOut">
            <template #icon><ZoomOutOutlined /></template>
          </Button>
          <Button size="small" @click="handleFitView">适应</Button>
          <Button size="small" @click="handleRefresh">
            <template #icon><ReloadOutlined /></template>
          </Button>
          <Button size="small" @click="toggleFullscreen">
            <template #icon>
              <FullscreenExitOutlined v-if="isFullscreen" />
              <FullscreenOutlined v-else />
            </template>
          </Button>
        </Space>
      </div>

      <!-- 图例 -->
      <div class="graph-legend mb-2">
        <Space :size="12" wrap>
          <div
            v-for="(item, key) in legendMap"
            :key="key"
            class="legend-item flex items-center"
          >
            <span
              class="legend-dot mr-1"
              :style="{ backgroundColor: item.color }"
            ></span>
            <span class="text-xs text-gray-600">{{ item.label }}</span>
          </div>
        </Space>
      </div>

      <!-- 图谱容器 -->
      <Card class="graph-card flex-1" :bordered="true" :bodyStyle="{ height: '100%', padding: 0 }">
        <Spin :spinning="loading" tip="加载中...">
          <div
            ref="graphContainer"
            class="graph-container h-full w-full"
            :style="{ minHeight: '400px' }"
          >
            <Empty
              v-if="!loading && (!graphData || graphData.nodes.length === 0)"
              description="暂无图谱数据，请先对文档进行图谱化处理"
              class="flex h-full flex-col items-center justify-center"
            />
          </div>
        </Spin>
      </Card>
    </div>
  </Modal>
</template>

<style lang="less" scoped>
.graph-wrapper {
  height: 100%;
}

.graph-legend {
  padding: 8px 12px;
  background: #fafafa;
  border-radius: 4px;
}

.legend-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.graph-card {
  overflow: hidden;

  :deep(.ant-card-body) {
    height: 100%;
  }
}

.graph-container {
  background: #f5f5f5;
}
</style>
