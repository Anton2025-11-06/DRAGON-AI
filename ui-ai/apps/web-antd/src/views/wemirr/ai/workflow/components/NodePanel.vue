<script setup lang="ts">
/**
 * NodePanel 组件
 * 节点面板，按类别展示可用的工作流节点供用户拖拽
 * 同步自后端 NodeType 枚举和工作流编排语义
 */
import type { NodeType } from '#/api/ai-workflow/types';

import { computed, onMounted, ref } from 'vue';

import {
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  CommentOutlined,
  EditOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  FormOutlined,
  MergeCellsOutlined,
  PlayCircleOutlined,
  RetweetOutlined,
  RobotOutlined,
  StopOutlined,
  SyncOutlined,
  TagsOutlined,
  ToolOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { storeToRefs } from 'pinia';

import { useAiWorkflowStore } from '#/store/ai-workflow';

import { workflowNodePanelCategories } from '../domain/node-panel-categories';

// Props
interface Props {
  /** 是否禁用拖拽 */
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
});

// Emits
const emit = defineEmits<{
  (e: 'drag-start', nodeType: NodeType, event: DragEvent): void;
}>();

// 当前悬停的节点
const hoveredNode = ref<NodeType | null>(null);
const loadErrorMessage = ref<null | string>(null);
const workflowStore = useAiWorkflowStore();
const { activeNodePanelItems, nodeDefinitionsError, nodeDefinitionsLoaded } =
  storeToRefs(workflowStore);

const categories = workflowNodePanelCategories;
const visibleNodeDefinitionError = computed(
  () => nodeDefinitionsError.value || loadErrorMessage.value,
);

// 展开的分类
const activeCategories = ref<string[]>(
  categories.map((category) => category.key),
);

onMounted(() => {
  if (!nodeDefinitionsLoaded.value) {
    void workflowStore.loadNodeDefinitions().catch((error) => {
      loadErrorMessage.value =
        error instanceof Error ? error.message : '加载后端节点定义失败';
    });
  }
});

// 图标组件映射
const iconComponents: Record<string, any> = {
  // 工作流边界图标
  PlayCircleOutlined,
  StopOutlined,
  EditOutlined,
  // 智能体图标
  RobotOutlined,
  BookOutlined,
  TagsOutlined,
  FormOutlined,
  UserOutlined,
  // 控制流图标
  BranchesOutlined,
  RetweetOutlined,
  MergeCellsOutlined,
  MergeOutlined: MergeCellsOutlined, // 别名
  SyncOutlined,
  ApartmentOutlined,
  // 能力图标
  CodeOutlined,
  FileTextOutlined,
  CommentOutlined,
  FileSearchOutlined,
  UnorderedListOutlined,
  // 外部系统图标
  ApiOutlined,
  ToolOutlined,
};

/**
 * 获取图标组件
 */
function getIconComponent(iconName: string) {
  return iconComponents[iconName] || CodeOutlined;
}

/**
 * 按分类获取节点列表
 */
function getNodesByCategory(categoryKey: string) {
  return activeNodePanelItems.value.filter(
    (item) => item.category === categoryKey,
  );
}

/**
 * 获取节点项样式 - 参考官网：统一蓝色边框，白底
 */
function getNodeItemStyle(nodeType: NodeType) {
  const isHovered = hoveredNode.value === nodeType;

  return {
    backgroundColor: '#fff',
    borderColor: '#5F95FF',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderRadius: '8px',
    cursor: props.disabled ? 'not-allowed' : 'grab',
    opacity: props.disabled ? 0.6 : 1,
    boxShadow:
      isHovered && !props.disabled
        ? '0 2px 8px rgba(95, 149, 255, 0.25)'
        : 'none',
    transform: isHovered && !props.disabled ? 'translateY(-1px)' : 'none',
  };
}

/**
 * 处理拖拽开始 - 使用 HTML5 拖放 API
 */
function handleDragStart(
  item: (typeof activeNodePanelItems.value)[number],
  event: DragEvent,
) {
  if (props.disabled) {
    event.preventDefault();
    return;
  }
  // 设置拖拽数据
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow-nodetype', item.type);
    event.dataTransfer.effectAllowed = 'move';
  }
  emit('drag-start', item.type, event);
}
</script>

<template>
  <div class="node-panel">
    <div class="node-panel-header">
      <span class="title">节点面板</span>
    </div>
    <a-alert
      v-if="visibleNodeDefinitionError"
      type="error"
      :message="visibleNodeDefinitionError"
      show-icon
      class="node-definition-error"
    />
    <a-collapse
      v-else
      v-model:active-key="activeCategories"
      :bordered="false"
      expand-icon-position="end"
      class="node-collapse"
    >
      <a-collapse-panel
        v-for="category in categories"
        :key="category.key"
        :header="category.label"
      >
        <template #extra>
          <span class="category-count">{{
            getNodesByCategory(category.key).length
          }}</span>
        </template>
        <div class="node-list">
          <div
            v-for="item in getNodesByCategory(category.key)"
            :key="item.type"
            class="node-item"
            data-testid="workflow-node-panel-item"
            :data-node-type="item.type"
            :style="getNodeItemStyle(item.type)"
            draggable="true"
            @dragstart="(e) => handleDragStart(item, e)"
            @mouseenter="hoveredNode = item.type"
            @mouseleave="hoveredNode = null"
          >
            <component :is="getIconComponent(item.icon)" class="node-icon" />
            <div class="node-info">
              <span class="node-label">{{ item.label }}</span>
              <span class="node-desc">{{ item.description }}</span>
            </div>
          </div>
        </div>
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>

<style scoped lang="less">
.node-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0; // 防止 flex 子元素撤破容器
  overflow: hidden;
  background-color: #fff;
}

.node-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;

  .title {
    font-size: 14px;
    font-weight: 500;
    color: #262626;
  }
}

.node-collapse {
  flex: 1;
  min-height: 0; // 关键：确保内容可以滚动
  // 兜底：展开内容超高时，把滚动容器钳制在可视区内（视口-编辑头-面板头），
  // 避免 flex 高度链异常时滚动区被内容撑破、滚动条滚不到底部节点
  max-height: calc(100vh - 112px);
  overflow-y: auto;
  background-color: #fff;

  :deep(.ant-collapse-item) {
    border-bottom: 1px solid #f0f0f0;

    .ant-collapse-header {
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 500;
      color: #595959;
    }

    .ant-collapse-content-box {
      padding: 8px 12px 12px;
    }
  }
}

.node-definition-error {
  margin: 12px;
}

.category-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  font-size: 11px;
  color: #8c8c8c;
  background-color: #f5f5f5;
  border-radius: 10px;
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.node-item {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  user-select: none;
  transition: all 0.2s ease;

  &:active {
    cursor: grabbing;
  }

  .node-icon {
    flex-shrink: 0;
    font-size: 18px;
    color: #595959;
  }

  .node-info {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 2px;
    min-width: 0;

    .node-label {
      font-size: 13px;
      font-weight: 500;
      color: #262626;
    }

    .node-desc {
      overflow: hidden;
      font-size: 11px;
      color: #8c8c8c;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}
</style>
