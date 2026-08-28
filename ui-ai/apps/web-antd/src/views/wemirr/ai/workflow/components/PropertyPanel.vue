<script setup lang="ts">
/**
 * PropertyPanel 组件
 * 属性面板，根据选中节点类型动态渲染配置表单
 * 支持所有 工作流节点类型
 * 集成 NodeTracePanel 显示调试信息
 *
 */
import type { Component } from 'vue';

import type { NodeType, WorkflowNode } from '#/api/ai-workflow/types';
import type { NodeTrace } from '#/store/debug-store';

import { computed, ref, watch } from 'vue';

import {
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  InboxOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  StopOutlined,
  SyncOutlined,
  ToolOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

// 调试组件
import NodeTracePanel from './debug/NodeTracePanel.vue';
import AgentNodeForm from './node-forms/AgentNodeForm.vue';
// 节点配置表单组件 - 能力节点
import CodeNodeForm from './node-forms/CodeNodeForm.vue';
import DocExtractorNodeForm from './node-forms/DocExtractorNodeForm.vue';
import EndNodeForm from './node-forms/EndNodeForm.vue';
// 节点配置表单组件 - 外部系统节点
import HttpNodeForm from './node-forms/HttpNodeForm.vue';
// 节点配置表单组件 - 控制流节点
import IfElseNodeForm from './node-forms/IfElseNodeForm.vue';
import IterationNodeForm from './node-forms/IterationNodeForm.vue';
import KnowledgeNodeForm from './node-forms/KnowledgeNodeForm.vue';
import ListOperatorNodeForm from './node-forms/ListOperatorNodeForm.vue';
// 节点配置表单组件 - 智能体节点
import LLMNodeForm from './node-forms/LLMNodeForm.vue';
import LoopNodeForm from './node-forms/LoopNodeForm.vue';
import ParallelNodeForm from './node-forms/ParallelNodeForm.vue';
import ParameterExtractorNodeForm from './node-forms/ParameterExtractorNodeForm.vue';
import QuestionClassifierNodeForm from './node-forms/QuestionClassifierNodeForm.vue';
// Vue Flow 自动处理端口更新，无需手动调用
// 节点配置表单组件 - 工作流边界
import StartNodeForm from './node-forms/StartNodeForm.vue';
import TemplateNodeForm from './node-forms/TemplateNodeForm.vue';
import ToolNodeForm from './node-forms/ToolNodeForm.vue';
import VariableAggregatorNodeForm from './node-forms/VariableAggregatorNodeForm.vue';
import VariableNodeForm from './node-forms/VariableNodeForm.vue';

// Props
interface Props {
  /** 选中的节点 */
  selectedNode?: null | WorkflowNode;
  /** 是否显示追踪信息 (调试模式) */
  showTrace?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  selectedNode: null,
  showTrace: false,
});

// Emits
const emit = defineEmits<{
  (e: 'delete-node', nodeId: string): void;
  (e: 'update-config', nodeId: string, config: Record<string, any>): void;
  (e: 'update-label', nodeId: string, label: string): void;
  (e: 'node-click', nodeId: string): void;
}>();

// Store
const workflowStore = useAiWorkflowStore();
const debugStore = useDebugStore();

// 节点标签
const nodeLabel = ref('');

// 节点描述
const nodeDescription = ref('');

// 节点配置
const nodeConfig = ref<Record<string, any>>({});

// 当前视图: config-配置, trace-追踪
const activeView = ref<'config' | 'trace'>('config');

// 视图选项
const viewOptions = [
  { label: '配置', value: 'config' },
  { label: '追踪', value: 'trace' },
];

/**
 * 图标组件映射
 * 按节点类型分类
 */
const iconComponents: Record<NodeType, Component> = {
  // 工作流边界
  START: PlayCircleOutlined,
  END: StopOutlined,
  VARIABLE_ASSIGNER: DatabaseOutlined,
  // 智能体节点
  LLM: RobotOutlined,
  KNOWLEDGE_RETRIEVAL: BookOutlined,
  QUESTION_CLASSIFIER: BranchesOutlined,
  PARAMETER_EXTRACTOR: ApiOutlined,
  AGENT: UserOutlined,
  // 控制流节点
  IF_ELSE: BranchesOutlined,
  LOOP: SyncOutlined,
  ITERATION: SyncOutlined,
  PARALLEL: ApartmentOutlined,
  VARIABLE_AGGREGATOR: ApartmentOutlined,
  // 能力节点
  CODE: CodeOutlined,
  TEMPLATE: FileTextOutlined,
  DOC_EXTRACTOR: BookOutlined,
  LIST_OPERATOR: UnorderedListOutlined,
  // 外部系统节点
  HTTP_REQUEST: ApiOutlined,
  TOOL: ToolOutlined,
};

/**
 * 节点配置表单组件映射
 * 根据节点类型动态加载对应的配置表单
 */
const nodeConfigForms: Partial<Record<NodeType, Component>> = {
  // 工作流边界
  START: StartNodeForm,
  END: EndNodeForm,
  VARIABLE_ASSIGNER: VariableNodeForm,
  // 智能体节点
  LLM: LLMNodeForm,
  KNOWLEDGE_RETRIEVAL: KnowledgeNodeForm,
  QUESTION_CLASSIFIER: QuestionClassifierNodeForm,
  PARAMETER_EXTRACTOR: ParameterExtractorNodeForm,
  AGENT: AgentNodeForm,
  // 控制流节点
  IF_ELSE: IfElseNodeForm,
  ITERATION: IterationNodeForm,
  VARIABLE_AGGREGATOR: VariableAggregatorNodeForm,
  LOOP: LoopNodeForm,
  PARALLEL: ParallelNodeForm,
  // 能力节点
  CODE: CodeNodeForm,
  TEMPLATE: TemplateNodeForm,
  DOC_EXTRACTOR: DocExtractorNodeForm,
  LIST_OPERATOR: ListOperatorNodeForm,
  // 外部系统节点
  HTTP_REQUEST: HttpNodeForm,
  TOOL: ToolNodeForm,
};

// 监听选中节点变化
watch(
  () => props.selectedNode,
  (node) => {
    if (node) {
      nodeLabel.value = node.label || '';
      nodeDescription.value = node.data?.description || '';
      nodeConfig.value = { ...node.data };
      // 如果在调试模式且有追踪数据，自动切换到追踪视图
      activeView.value =
        props.showTrace && debugStore.getNodeTrace(node.id)
          ? 'trace'
          : 'config';
    } else {
      nodeLabel.value = '';
      nodeDescription.value = '';
      nodeConfig.value = {};
      activeView.value = 'config';
    }
  },
  { immediate: true, deep: true },
);

// 监听 showTrace 变化
watch(
  () => props.showTrace,
  (show) => {
    if (!show) {
      activeView.value = 'config';
    }
  },
);

/**
 * 当前节点的追踪数据
 */
const currentNodeTrace = computed((): NodeTrace | null => {
  if (!props.selectedNode) return null;
  return debugStore.getNodeTrace(props.selectedNode.id) || null;
});

/**
 * 获取节点类型标签
 */
function getNodeTypeLabel(): string {
  if (!props.selectedNode) return '';
  return (
    workflowStore.getActiveNodeDefinition(props.selectedNode.type)
      ?.displayName ||
    props.selectedNode.label ||
    props.selectedNode.type
  );
}

/**
 * 获取节点图标组件
 */
function getNodeIcon(): Component {
  if (!props.selectedNode) return PlayCircleOutlined;
  return iconComponents[props.selectedNode.type] || CodeOutlined;
}

/**
 * 获取节点类型徽章样式
 */
function getNodeTypeBadgeStyle() {
  if (!props.selectedNode) return {};
  const activeDefinition = workflowStore.getActiveNodeDefinition(
    props.selectedNode.type,
  );
  const colors = activeDefinition
    ? { fill: `${activeDefinition.color}14`, stroke: activeDefinition.color }
    : { fill: '#fff1f0', stroke: '#ff4d4f' };
  return {
    backgroundColor: colors.fill,
    borderColor: colors.stroke,
    color: colors.stroke,
  };
}

/**
 * 计算属性：获取配置表单组件
 * 根据节点类型动态返回对应的表单组件
 */
const configFormComponent = computed<Component | null>(() => {
  if (!props.selectedNode) return null;
  return nodeConfigForms[props.selectedNode.type] || null;
});

/**
 * 处理标签变更
 */
function handleLabelChange() {
  if (props.selectedNode && nodeLabel.value) {
    emit('update-label', props.selectedNode.id, nodeLabel.value);
  }
}

/**
 * 处理描述变更
 */
function handleDescriptionChange() {
  if (props.selectedNode) {
    const updatedConfig = {
      ...nodeConfig.value,
      description: nodeDescription.value,
    };
    handleConfigUpdate(updatedConfig);
  }
}

/**
 * 处理配置更新
 */
function handleConfigUpdate(config: Record<string, any>) {
  nodeConfig.value = config;
  if (props.selectedNode) {
    emit('update-config', props.selectedNode.id, config);
    workflowStore.updateNodeConfig(props.selectedNode.id, config);
    // Vue Flow 会自动响应数据变化，无需手动更新端口
  }
}

/**
 * 处理删除节点
 */
function handleDeleteNode() {
  if (props.selectedNode) {
    emit('delete-node', props.selectedNode.id);
  }
}

/**
 * 处理追踪面板中的节点点击
 */
function handleTraceNodeClick(nodeId: string) {
  emit('node-click', nodeId);
}
</script>

<template>
  <div
    class="property-panel"
    data-testid="workflow-property-panel"
    :data-selected-node-id="selectedNode?.id || ''"
    :data-selected-node-type="selectedNode?.type || ''"
  >
    <div class="property-panel-header">
      <span class="title">{{ selectedNode ? '节点配置' : '属性面板' }}</span>
      <div class="header-actions">
        <!-- 调试模式下显示追踪切换 -->
        <a-segmented
          v-if="selectedNode && showTrace"
          v-model:value="activeView"
          :options="viewOptions"
          size="small"
          class="view-toggle"
        />
        <a-button
          v-if="selectedNode"
          type="text"
          size="small"
          danger
          @click="handleDeleteNode"
        >
          <template #icon>
            <DeleteOutlined />
          </template>
        </a-button>
      </div>
    </div>

    <div v-if="!selectedNode" class="empty-state">
      <InboxOutlined class="empty-icon" />
      <span class="empty-text">请选择一个节点进行配置</span>
    </div>

    <div
      v-else
      class="property-content"
      data-testid="workflow-property-content"
    >
      <!-- 配置视图 -->
      <template v-if="activeView === 'config'">
        <!-- 节点基本信息 -->
        <div class="node-info-section">
          <div class="node-type-badge" :style="getNodeTypeBadgeStyle()">
            <component :is="getNodeIcon()" class="type-icon" />
            <span>{{ getNodeTypeLabel() }}</span>
          </div>
          <a-form layout="vertical" :model="nodeConfig" class="basic-form">
            <a-form-item label="节点名称">
              <a-input
                v-model:value="nodeLabel"
                placeholder="请输入节点名称"
                @change="handleLabelChange"
              />
            </a-form-item>
            <a-form-item
              v-if="
                selectedNode.type !== 'START' && selectedNode.type !== 'END'
              "
              label="节点描述"
            >
              <a-textarea
                v-model:value="nodeDescription"
                placeholder="请输入节点描述（可选）"
                :rows="2"
                @change="handleDescriptionChange"
              />
            </a-form-item>
          </a-form>
        </div>

        <a-divider style="margin: 12px 0" />

        <!-- 动态节点配置表单 -->
        <div class="node-config-section">
          <component
            :is="configFormComponent"
            v-if="configFormComponent"
            :config="nodeConfig"
            :node-id="selectedNode.id"
            @update:config="handleConfigUpdate"
          />
          <div v-else class="no-config">
            <span>该节点无需额外配置</span>
          </div>
        </div>
      </template>

      <!-- 追踪视图 (调试模式) -->
      <template v-else-if="activeView === 'trace'">
        <NodeTracePanel
          :trace="currentNodeTrace"
          @node-click="handleTraceNodeClick"
        />
      </template>
    </div>
  </div>
</template>

<style scoped lang="less">
.property-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0; // 防止 flex 子元素撤破容器
  overflow: hidden;
  background-color: #fff;
}

.property-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid #f0f0f0;

  .title {
    font-size: 15px;
    font-weight: 600;
    color: #262626;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;

    .view-toggle {
      :deep(.ant-segmented-item) {
        min-width: 48px;
      }
    }
  }
}

.empty-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;

  .empty-icon {
    font-size: 48px;
    color: #d9d9d9;
  }

  .empty-text {
    font-size: 13px;
    color: #8c8c8c;
  }
}

.property-content {
  flex: 1;
  min-height: 0; // 关键：确保内容可以滚动
  padding: 18px 20px 24px;
  overflow-y: auto;
  overflow-x: hidden;
}

.node-info-section {
  .node-type-badge {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 4px 12px;
    margin-bottom: 12px;
    font-size: 12px;
    font-weight: 500;
    border: 1px solid;
    border-radius: 16px;

    .type-icon {
      font-size: 14px;
    }
  }

  .basic-form {
    :deep(.ant-form-item) {
      margin-bottom: 12px;
    }

    :deep(.ant-form-item-label) {
      padding-bottom: 4px;

      > label {
        font-size: 12px;
        color: #595959;
      }
    }

    :deep(.ant-input-textarea) {
      textarea {
        resize: none;
      }
    }
  }
}

.node-config-section {
  .no-config {
    padding: 20px;
    font-size: 13px;
    color: #8c8c8c;
    text-align: center;
    background-color: #fafafa;
    border-radius: 6px;
  }

  // 表单组件通用样式
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  :deep(.ant-select) {
    width: 100%;
  }

  :deep(.ant-input-number) {
    width: 100%;
  }

  // 分组标题样式
  :deep(.config-section-title) {
    margin: 16px 0 8px;
    font-size: 13px;
    font-weight: 500;
    color: #262626;
  }

  // 帮助文本样式
  :deep(.config-help-text) {
    margin-top: 4px;
    font-size: 12px;
    color: #8c8c8c;
  }
}

// 暗色模式适配
html[class='dark'] {
  .property-panel {
    background-color: var(--ant-color-bg-container);
  }

  .property-panel-header {
    border-bottom-color: var(--ant-color-border);

    .title {
      color: var(--ant-color-text);
    }
  }

  .empty-state {
    .empty-icon {
      color: var(--ant-color-text-quaternary);
    }

    .empty-text {
      color: var(--ant-color-text-secondary);
    }
  }

  .node-config-section {
    .no-config {
      background-color: var(--ant-color-bg-layout);
      color: var(--ant-color-text-secondary);
    }
  }
}
</style>
