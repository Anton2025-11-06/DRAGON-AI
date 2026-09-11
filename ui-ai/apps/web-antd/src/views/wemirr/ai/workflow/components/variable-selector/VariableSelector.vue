<template>
  <a-popover
    v-model:open="popoverVisible"
    trigger="click"
    placement="bottomLeft"
    :overlay-style="{ width: '320px' }"
  >
    <template #content>
      <div class="variable-selector">
        <!-- 搜索框 -->
        <a-input-search
          v-model:value="searchText"
          placeholder="搜索变量"
          size="small"
          allow-clear
          class="search-input"
        />

        <!-- 变量列表 -->
        <div class="variable-list">
          <template v-if="filteredNodes.length > 0">
            <div
              v-for="node in filteredNodes"
              :key="node.id"
              class="node-group"
            >
              <!-- 节点头部 -->
              <div class="node-header" @click="toggleNodeExpand(node.id)">
                <component :is="getNodeIcon(node.type)" class="node-icon" />
                <span class="node-name">{{ node.label || node.id }}</span>
                <span class="variable-count">{{ node.variables.length }}</span>
                <RightOutlined
                  class="expand-icon"
                  :class="{ expanded: expandedNodes.has(node.id) }"
                />
              </div>

              <!-- 变量列表 -->
              <div v-show="expandedNodes.has(node.id)" class="variables">
                <div
                  v-for="variable in node.variables"
                  :key="variable.name"
                  class="variable-item"
                  @click="selectVariable(node, variable)"
                >
                  <span class="var-icon">{x}</span>
                  <span class="var-name">{{ variable.name }}</span>
                  <a-tag size="small" :color="getTypeColor(variable.type)">
                    {{ variable.type }}
                  </a-tag>
                </div>
              </div>
            </div>
          </template>

          <!-- 空状态 -->
          <div v-else class="empty-state">
            <InboxOutlined class="empty-icon" />
            <span class="empty-text">
              {{ searchText ? '未找到匹配的变量' : '暂无可用变量' }}
            </span>
          </div>
        </div>
      </div>
    </template>

    <!-- 触发按钮 -->
    <slot>
      <a-button type="text" size="small" class="trigger-btn">
        <template #icon><PlusOutlined /></template>
        {{ buttonText }}
      </a-button>
    </slot>
  </a-popover>
</template>

<script setup lang="ts">
/**
 * VariableSelector 变量选择器组件
 * 显示上游节点输出变量，支持搜索过滤，插入变量引用
 * Requirements: 7.7
 */
import type { Component } from 'vue';
import type { NodeType, ExtendedVariableType } from '#/api/ai-workflow/types';

import { computed, ref, watch } from 'vue';
import {
  PlusOutlined,
  RightOutlined,
  InboxOutlined,
  PlayCircleOutlined,
  StopOutlined,
  RobotOutlined,
  BookOutlined,
  ToolOutlined,
  UserOutlined,
  BranchesOutlined,
  SyncOutlined,
  ApartmentOutlined,
  ApiOutlined,
  CodeOutlined,
  CommentOutlined,
  DatabaseOutlined,
} from '@ant-design/icons-vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';
import { buildWorkflowVariableReference } from './variable-reference';

// ==================== 类型定义 ====================

/** 节点输出变量 */
export interface NodeVariable {
  /** 变量名 */
  name: string;
  /** 变量类型 */
  type: ExtendedVariableType;
  /** 描述 */
  description?: string;
}

/** 节点及其变量 */
export interface NodeWithVariables {
  /** 节点ID */
  id: string;
  /** 节点标签 */
  label: string;
  /** 节点类型 */
  type: NodeType;
  /** 输出变量列表 */
  variables: NodeVariable[];
}

// ==================== Props & Emits ====================

interface Props {
  /** 当前节点ID（用于过滤上游节点） */
  currentNodeId?: string;
  /** 按钮文本 */
  buttonText?: string;
  /** 是否只显示特定类型的变量 */
  filterTypes?: ExtendedVariableType[];
}

const props = withDefaults(defineProps<Props>(), {
  currentNodeId: '',
  buttonText: '插入变量',
  filterTypes: () => [],
});

const emit = defineEmits<{
  /** 选择变量时触发 */
  (
    e: 'select',
    reference: string,
    variable: NodeVariable,
    node: NodeWithVariables,
  ): void;
}>();

// ==================== Store ====================

const workflowStore = useAiWorkflowStore();

// ==================== 状态 ====================

/** 弹出框可见性 */
const popoverVisible = ref(false);

/** 搜索文本 */
const searchText = ref('');

/** 展开的节点 */
const expandedNodes = ref<Set<string>>(new Set());

// ==================== 图标映射 ====================

const iconComponents: Record<string, Component> = {
  START: PlayCircleOutlined,
  END: StopOutlined,
  LLM: RobotOutlined,
  KNOWLEDGE_RETRIEVAL: BookOutlined,
  TOOL: ToolOutlined,
  AGENT: UserOutlined,
  IF_ELSE: BranchesOutlined,
  LOOP: SyncOutlined,
  ITERATION: SyncOutlined,
  PARALLEL: ApartmentOutlined,
  HTTP_REQUEST: ApiOutlined,
  CODE: CodeOutlined,
  VARIABLE_ASSIGNER: DatabaseOutlined,
  VARIABLE_AGGREGATOR: ApartmentOutlined,
  QUESTION_CLASSIFIER: BranchesOutlined,
  PARAMETER_EXTRACTOR: ApiOutlined,
  TEMPLATE: CodeOutlined,
  REPLY: CommentOutlined,
  DOC_EXTRACTOR: BookOutlined,
  LIST_OPERATOR: DatabaseOutlined,
};

// ==================== 类型颜色映射 ====================

const typeColors: Record<string, string> = {
  string: 'blue',
  number: 'green',
  boolean: 'orange',
  object: 'purple',
  array: 'cyan',
  'Array[string]': 'cyan',
  'Array[number]': 'cyan',
  'Array[File]': 'magenta',
  File: 'magenta',
};

// ==================== 计算属性 ====================

/**
 * 获取上游节点及其输出变量
 */
const upstreamNodes = computed<NodeWithVariables[]>(() => {
  const canvas = workflowStore.canvasRef;
  if (!canvas || !props.currentNodeId) return getAllNodes();

  // 获取所有上游节点
  const upstreamNodeIds = getUpstreamNodeIds(props.currentNodeId);
  const nodes = canvas.getNodes() || [];

  return nodes
    .filter((node: any) => upstreamNodeIds.has(node.id))
    .map((node: any) => {
      const nodeType = node.data?.nodeType as NodeType;
      return {
        id: node.id,
        label: node.data?.label || node.id,
        type: nodeType,
        variables: getNodeOutputVariables(nodeType, node.data?.config || {}),
      };
    })
    .filter((node: any) => node.variables.length > 0);
});

/**
 * 获取所有节点（当没有指定当前节点时）
 */
function getAllNodes(): NodeWithVariables[] {
  const canvas = workflowStore.canvasRef;
  if (!canvas) return [];

  const nodes = canvas.getNodes() || [];
  return nodes
    .map((node: any) => {
      const nodeType = node.data?.nodeType as NodeType;
      return {
        id: node.id,
        label: node.data?.label || node.id,
        type: nodeType,
        variables: getNodeOutputVariables(nodeType, node.data?.config || {}),
      };
    })
    .filter((node: any) => node.variables.length > 0);
}

/**
 * 过滤后的节点列表
 */
const filteredNodes = computed<NodeWithVariables[]>(() => {
  let nodes = upstreamNodes.value;

  // 按搜索文本过滤
  if (searchText.value) {
    const search = searchText.value.toLowerCase();
    nodes = nodes
      .map((node) => ({
        ...node,
        variables: node.variables.filter(
          (v) =>
            v.name.toLowerCase().includes(search) ||
            (v.description && v.description.toLowerCase().includes(search)),
        ),
      }))
      .filter((node) => node.variables.length > 0);
  }

  // 按类型过滤
  if (props.filterTypes.length > 0) {
    nodes = nodes
      .map((node) => ({
        ...node,
        variables: node.variables.filter((v) =>
          props.filterTypes.includes(v.type),
        ),
      }))
      .filter((node) => node.variables.length > 0);
  }

  return nodes;
});

// ==================== 方法 ====================

/**
 * 获取上游节点ID集合
 */
function getUpstreamNodeIds(nodeId: string): Set<string> {
  const canvas = workflowStore.canvasRef;
  if (!canvas) return new Set();

  const edges = canvas.getEdges() || [];
  const visited = new Set<string>();
  const queue: string[] = [];

  // 获取直接上游节点
  edges.forEach((edge: any) => {
    if (edge.target === nodeId && edge.source && !visited.has(edge.source)) {
      visited.add(edge.source);
      queue.push(edge.source);
    }
  });

  // BFS 遍历所有上游节点
  while (queue.length > 0) {
    const currentId = queue.shift()!;
    edges.forEach((edge: any) => {
      if (
        edge.target === currentId &&
        edge.source &&
        !visited.has(edge.source)
      ) {
        visited.add(edge.source);
        queue.push(edge.source);
      }
    });
  }

  return visited;
}

/**
 * 根据节点类型获取输出变量
 */
function getNodeOutputVariables(
  nodeType: NodeType,
  config: Record<string, any>,
): NodeVariable[] {
  const variables: NodeVariable[] = [];

  switch (nodeType) {
    case 'START':
      // START 节点的输出是其定义的输入字段
      if (config.fields && Array.isArray(config.fields)) {
        config.fields.forEach((field: any) => {
          variables.push({
            name: field.name,
            type: mapInputFieldType(field.type),
            description: field.description || field.label,
          });
        });
      }
      break;

    case 'LLM':
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: 'LLM 输出内容',
      });
      if (config.structuredOutput?.enabled) {
        variables.push({
          name: 'structured_output',
          type: 'object',
          description: '结构化输出',
        });
      }
      break;

    case 'KNOWLEDGE_RETRIEVAL':
      variables.push({
        name: config.outputVariable || 'results',
        type: 'array',
        description: '检索结果列表',
      });
      break;

    case 'QUESTION_CLASSIFIER':
      variables.push({
        name: 'category',
        type: 'string',
        description: '分类结果',
      });
      variables.push({
        name: 'selectedBranch',
        type: 'string',
        description: '选中的分支ID',
      });
      break;

    case 'PARAMETER_EXTRACTOR':
      if (config.parameters && Array.isArray(config.parameters)) {
        config.parameters.forEach((param: any) => {
          variables.push({
            name: param.name,
            type: param.type || 'string',
            description: param.description,
          });
        });
      }
      variables.push({
        name: '__is_success',
        type: 'boolean',
        description: '提取是否成功',
      });
      variables.push({
        name: '__reason',
        type: 'string',
        description: '失败原因',
      });
      break;

    case 'CODE':
      // 代码节点输出固定为 { result: <返回值> }（参照 MaxKB ToolExecutor）
      variables.push({
        name: 'result',
        type: 'object',
        description: '代码执行返回值（类型不限）',
      });
      break;

    case 'HTTP_REQUEST':
      variables.push({
        name: config.outputVariable || 'response',
        type: 'object',
        description: 'HTTP 响应',
      });
      variables.push({
        name: 'status',
        type: 'number',
        description: 'HTTP 状态码',
      });
      variables.push({
        name: 'headers',
        type: 'object',
        description: '响应头',
      });
      variables.push({
        name: 'body',
        type: 'object',
        description: '响应体',
      });
      break;

    case 'ITERATION':
      variables.push({
        name: config.outputVariable || 'results',
        type: 'array',
        description: '迭代结果数组',
      });
      // 迭代内部变量
      variables.push({
        name: 'item',
        type: 'object',
        description: '当前迭代元素',
      });
      variables.push({
        name: 'index',
        type: 'number',
        description: '当前迭代索引',
      });
      break;

    case 'VARIABLE_AGGREGATOR':
      if (config.groups && Array.isArray(config.groups)) {
        config.groups.forEach((group: any) => {
          variables.push({
            name: group.outputVariable,
            type: group.variableType || 'object',
            description: '聚合变量',
          });
        });
      }
      break;

    case 'TEMPLATE':
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: '模板渲染结果',
      });
      break;

    case 'REPLY':
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: '回复内容',
      });
      break;

    case 'DOC_EXTRACTOR':
      variables.push({
        name: config.outputVariable || 'text',
        type: 'string',
        description: '提取的文本内容',
      });
      if (config.extractMetadata) {
        variables.push({
          name: 'metadata',
          type: 'object',
          description: '文档元数据',
        });
      }
      break;

    case 'LIST_OPERATOR':
      variables.push({
        name: config.outputVariable || 'result',
        type: 'array',
        description: '列表操作结果',
      });
      break;

    case 'VARIABLE_ASSIGNER':
      if (config.assignments && Array.isArray(config.assignments)) {
        config.assignments.forEach((assignment: any) => {
          variables.push({
            name: assignment.variableName,
            type: assignment.variableType || 'string',
            description: assignment.description,
          });
        });
      }
      break;

    case 'TOOL':
      variables.push({
        name: config.outputVariable || 'result',
        type: 'object',
        description: '工具执行结果',
      });
      break;

    case 'AGENT':
      variables.push({
        name: config.outputVariable || 'response',
        type: 'string',
        description: '智能体响应',
      });
      break;

    default:
      // 默认输出
      variables.push({
        name: 'output',
        type: 'object',
        description: '节点输出',
      });
  }

  return variables;
}

/**
 * 映射输入字段类型到变量类型
 */
function mapInputFieldType(fieldType: string): ExtendedVariableType {
  const typeMap: Record<string, ExtendedVariableType> = {
    SHORT_TEXT: 'string',
    PARAGRAPH: 'string',
    NUMBER: 'number',
    SELECT: 'string',
    CHECKBOX: 'boolean',
    SINGLE_FILE: 'File',
    FILE_LIST: 'Array[File]',
  };
  return typeMap[fieldType] || 'string';
}

/**
 * 获取节点图标
 */
function getNodeIcon(nodeType: NodeType): Component {
  return iconComponents[nodeType] || CodeOutlined;
}

/**
 * 获取类型颜色
 */
function getTypeColor(type: ExtendedVariableType): string {
  return typeColors[type] || 'default';
}

/**
 * 切换节点展开状态
 */
function toggleNodeExpand(nodeId: string) {
  if (expandedNodes.value.has(nodeId)) {
    expandedNodes.value.delete(nodeId);
  } else {
    expandedNodes.value.add(nodeId);
  }
  // 触发响应式更新
  expandedNodes.value = new Set(expandedNodes.value);
}

/**
 * 选择变量
 * 使用后端运行时 scope 构建变量引用。
 */
function selectVariable(node: NodeWithVariables, variable: NodeVariable) {
  const reference = buildWorkflowVariableReference(node, variable);
  emit('select', reference, variable, node);
  popoverVisible.value = false;
}

// ==================== 监听 ====================

// 打开弹出框时，默认展开第一个节点
watch(popoverVisible, (visible) => {
  if (visible && filteredNodes.value.length > 0) {
    expandedNodes.value = new Set([filteredNodes.value[0]!.id]);
  }
});

// 暴露方法供外部调用
defineExpose({
  open: () => {
    popoverVisible.value = true;
  },
  close: () => {
    popoverVisible.value = false;
  },
});
</script>

<style scoped lang="less">
.variable-selector {
  max-height: 400px;
  overflow: hidden;
  display: flex;
  flex-direction: column;

  .search-input {
    margin-bottom: 8px;
  }

  .variable-list {
    flex: 1;
    overflow-y: auto;
    max-height: 350px;
  }

  .node-group {
    margin-bottom: 4px;
    border: 1px solid #f0f0f0;
    border-radius: 6px;
    overflow: hidden;

    .node-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background-color: #fafafa;
      cursor: pointer;
      transition: background-color 0.2s;

      &:hover {
        background-color: #f0f0f0;
      }

      .node-icon {
        font-size: 14px;
        color: #1890ff;
      }

      .node-name {
        flex: 1;
        font-size: 13px;
        font-weight: 500;
        color: #262626;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .variable-count {
        font-size: 11px;
        color: #8c8c8c;
        background-color: #e6e6e6;
        padding: 1px 6px;
        border-radius: 10px;
      }

      .expand-icon {
        font-size: 10px;
        color: #8c8c8c;
        transition: transform 0.2s;

        &.expanded {
          transform: rotate(90deg);
        }
      }
    }

    .variables {
      border-top: 1px solid #f0f0f0;
    }

    .variable-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px 8px 24px;
      cursor: pointer;
      transition: background-color 0.2s;

      &:hover {
        background-color: #e6f7ff;
      }

      .var-icon {
        font-size: 12px;
        font-weight: 600;
        color: #722ed1;
        background-color: #f9f0ff;
        padding: 2px 4px;
        border-radius: 3px;
      }

      .var-name {
        flex: 1;
        font-size: 12px;
        color: #595959;
        font-family:
          'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      }
    }
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 32px 16px;
    color: #8c8c8c;

    .empty-icon {
      font-size: 32px;
      margin-bottom: 8px;
    }

    .empty-text {
      font-size: 12px;
    }
  }
}

.trigger-btn {
  color: #1890ff;

  &:hover {
    color: #40a9ff;
  }
}
</style>
