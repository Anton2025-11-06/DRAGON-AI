<script setup lang="ts">
/**
 * VariableSelector 变量选择器组件
 * 显示上游节点输出变量，支持搜索过滤，插入变量引用
 * Requirements: 7.7
 *
 * 「哪些变量可见」（上游回溯 / 审批透传）的推导在 ./upstream-variables.ts，
 * 与审批节点表单的「选择输出参数」候选共用一份，不在此处另写一套。
 */
import type { Component } from 'vue';

import type {
  GraphNode,
  NodeVariable,
  NodeWithVariables,
} from './upstream-variables';

import type { ExtendedVariableType, NodeType } from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import {
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  CodeOutlined,
  CommentOutlined,
  DatabaseOutlined,
  InboxOutlined,
  MergeCellsOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RightOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
  SyncOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';

import {
  nodeOutputVariables,
  readGraph,
  upstreamNodeIds,
} from './upstream-variables';
import {
  buildWorkflowVariableReference,
  normalizeVariablePathSuffix,
} from './variable-reference';

// ==================== Props & Emits ====================

interface Props {
  /** 当前节点ID（用于过滤上游节点） */
  currentNodeId?: string;
  /** 按钮文本 */
  buttonText?: string;
  /** 是否只显示特定类型的变量 */
  filterTypes?: ExtendedVariableType[];
  /** 是否允许在变量后追加 JSON 路径/下标再次提取（如 .data.list[0]） */
  enablePath?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  currentNodeId: '',
  buttonText: '插入变量',
  filterTypes: () => [],
  enablePath: false,
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

/** 子路径输入草稿（按「节点+变量」隔离） */
const pathDraft = reactive<Record<string, string>>({});

/** 当前展开子路径输入的变量行 key */
const editingPathKey = ref('');

// ==================== 图标映射 ====================

const iconComponents: Record<string, Component> = {
  START: PlayCircleOutlined,
  END: StopOutlined,
  LLM: RobotOutlined,
  KNOWLEDGE_RETRIEVAL: BookOutlined,
  TOOL: ToolOutlined,
  MCP_TOOL: CloudServerOutlined,
  APPROVAL: SafetyCertificateOutlined,
  IF_ELSE: BranchesOutlined,
  LOOP: SyncOutlined,
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
  WORKFLOW: MergeCellsOutlined,
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

/** 画布拓扑的归一视图（节点类型/配置/边），上游与透传推导都吃它 */
const graph = computed(() => readGraph(workflowStore.canvasRef));

function toWithVariables(node: GraphNode): NodeWithVariables {
  return {
    id: node.id,
    label: node.label,
    type: node.type,
    variables: nodeOutputVariables(node, graph.value),
  };
}

/**
 * 获取上游节点及其输出变量
 */
const upstreamNodes = computed<NodeWithVariables[]>(() => {
  if (!workflowStore.canvasRef) return [];
  if (!props.currentNodeId) return getAllNodes();

  // 获取所有上游节点
  const visible = upstreamNodeIds(graph.value, props.currentNodeId);
  return graph.value.nodes
    .filter((node) => visible.has(node.id))
    .map((node) => toWithVariables(node))
    .filter((node) => node.variables.length > 0);
});

/**
 * 获取所有节点（当没有指定当前节点时）
 */
function getAllNodes(): NodeWithVariables[] {
  if (!workflowStore.canvasRef) return [];
  return graph.value.nodes
    .map((node) => toWithVariables(node))
    .filter((node) => node.variables.length > 0);
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

  // 按类型过滤（类型族匹配，见 typeMatchesFilter）
  if (props.filterTypes.length > 0) {
    nodes = nodes
      .map((node) => ({
        ...node,
        variables: node.variables.filter((v) =>
          typeMatchesFilter(v.type, props.filterTypes),
        ),
      }))
      .filter((node) => node.variables.length > 0);
  }

  return nodes;
});

// ==================== 方法 ====================

/**
 * 变量类型是否命中过滤条件（按「类型族」匹配，不做字面量全等）。
 *
 * 之前用 filterTypes.includes(v.type) 精确比较，导致 Array[string]/Array[File]
 * 等带元素类型的数组、以及 CODE/HTTP 这类声明为 object 但实际可返回数组的
 * 输出全被过滤掉，列表处理节点的「输入数组」里选不到任何上游变量。
 */
function typeMatchesFilter(
  type: ExtendedVariableType,
  filterTypes: ExtendedVariableType[],
): boolean {
  if (filterTypes.length === 0) return true;
  const actual = (type ?? '').trim().toLowerCase();
  const arrayLike = actual === 'array' || actual.startsWith('array[');
  return filterTypes.some((want) => {
    const wanted = (want ?? '').trim().toLowerCase();
    // 要求数组：各种 Array[x] 均命中；object 也放行（代码/HTTP 返回数组很常见）
    if (wanted === 'array') return arrayLike || actual === 'object';
    return actual === wanted;
  });
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

/**
 * 子路径输入行的唯一 key
 */
function pathRowKey(node: NodeWithVariables, variable: NodeVariable): string {
  return `${node.id}::${variable.name}`;
}

/**
 * 展开/收起某变量的子路径输入
 */
function togglePathEdit(node: NodeWithVariables, variable: NodeVariable) {
  const key = pathRowKey(node, variable);
  editingPathKey.value = editingPathKey.value === key ? '' : key;
  if (pathDraft[key] === undefined) {
    pathDraft[key] = '';
  }
}

/** 当前行的子路径后缀；null 表示写法非法 */
function draftPathSuffix(key: string): null | string {
  return normalizeVariablePathSuffix(pathDraft[key] || '');
}

/**
 * 子路径输入下的实时提示（预览将插入的引用 / 写法错误）
 */
function pathHintText(node: NodeWithVariables, variable: NodeVariable): string {
  const key = pathRowKey(node, variable);
  const suffix = draftPathSuffix(key);
  if (suffix === null) {
    return '只支持字段名、. 与 [下标]，如 data.list[0]';
  }
  if (!(pathDraft[key] || '').trim()) {
    return '留空即引用整个变量；可填 data.list 或 [0]（上游是 JSON 文本时会自动解析后取值）';
  }
  return `将插入：${node.label || node.id}.${variable.name}${suffix}`;
}

/**
 * 按「变量 + 子路径」插入引用
 */
function insertWithPath(node: NodeWithVariables, variable: NodeVariable) {
  const suffix = draftPathSuffix(pathRowKey(node, variable));
  if (suffix === null) {
    return; // 提示文案已给出，等用户改正写法
  }
  const reference = buildWorkflowVariableReference(node, variable, suffix);
  emit('select', reference, variable, node);
  editingPathKey.value = '';
  popoverVisible.value = false;
}

// ==================== 监听 ====================

// 打开弹出框时，默认展开第一个节点（并收起上次的子路径输入）
watch(popoverVisible, (visible) => {
  if (!visible) {
    return;
  }
  editingPathKey.value = '';
  if (filteredNodes.value.length > 0) {
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
                >
                  <div
                    class="variable-row"
                    @click="selectVariable(node, variable)"
                  >
                    <span class="var-icon">{x}</span>
                    <span class="var-name">{{ variable.name }}</span>
                    <a-tag size="small" :color="getTypeColor(variable.type)">
                      {{ variable.type }}
                    </a-tag>
                    <span
                      v-if="enablePath"
                      class="path-toggle"
                      :class="{
                        active: editingPathKey === pathRowKey(node, variable),
                      }"
                      @click.stop="togglePathEdit(node, variable)"
                    >
                      路径
                    </span>
                  </div>
                  <div
                    v-if="editingPathKey === pathRowKey(node, variable)"
                    class="path-editor"
                    @click.stop
                  >
                    <div class="path-editor-row">
                      <a-input
                        v-model:value="pathDraft[editingPathKey]"
                        size="small"
                        placeholder="子路径，如 data.list[0]"
                        @press-enter="insertWithPath(node, variable)"
                      />
                      <a-button
                        size="small"
                        type="primary"
                        @click="insertWithPath(node, variable)"
                      >
                        插入
                      </a-button>
                    </div>
                    <div class="path-hint">
                      {{ pathHintText(node, variable) }}
                    </div>
                  </div>
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
      .variable-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px 8px 24px;
        cursor: pointer;
        transition: background-color 0.2s;

        &:hover {
          background-color: #e6f7ff;
        }
      }

      .path-toggle {
        flex-shrink: 0;
        padding: 0 5px;
        font-size: 11px;
        line-height: 16px;
        color: #1890ff;
        border: 1px solid #91d5ff;
        border-radius: 3px;

        &:hover,
        &.active {
          color: #fff;
          background-color: #1890ff;
        }
      }

      .path-editor {
        padding: 6px 12px 8px 24px;

        .path-editor-row {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .path-hint {
          margin-top: 4px;
          font-size: 11px;
          line-height: 16px;
          color: #8c8c8c;
        }
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
