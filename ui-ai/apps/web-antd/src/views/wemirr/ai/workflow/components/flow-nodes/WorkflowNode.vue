<script setup lang="ts">
import type { NodeProps } from '@vue-flow/core';

/**
 * WorkflowNode - Vue Flow 自定义工作流节点
 * 支持所有节点类型，包括多分支节点 (IF_ELSE, QUESTION_CLASSIFIER)
 */
import type { NodeType } from '#/api/ai-workflow/types';

import { computed } from 'vue';

import {
  ApartmentOutlined,
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  StopOutlined,
  SyncOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { Handle, Position } from '@vue-flow/core';

import { useAiWorkflowStore } from '#/store/ai-workflow';

import { outputHandleForBranch } from '../../domain/ports';

// ==================== Props ====================

/**
 * 节点数据类型
 */
interface NodeData {
  nodeType: NodeType;
  label: string;
  config: Record<string, any>;
  executionStatus?: 'completed' | 'failed' | 'pending' | 'running' | null;
  executionDuration?: null | number;
}

/**
 * 自定义节点 Props - 继承 Vue Flow NodeProps
 */
interface Props extends NodeProps<NodeData> {}

const props = defineProps<Props>();
const workflowStore = useAiWorkflowStore();

// ==================== 计算属性 ====================

const nodeType = computed(() => props.data.nodeType);
const config = computed(() => props.data.config || {});

/** 节点样式 */
const nodeStyle = computed(() => {
  const activeDefinition = workflowStore.getActiveNodeDefinition(
    nodeType.value,
  );
  const colors = activeDefinition
    ? { fill: `${activeDefinition.color}14`, stroke: activeDefinition.color }
    : { fill: '#fff1f0', stroke: '#ff4d4f' };
  return {
    '--node-bg': colors.fill,
    '--node-border': colors.stroke,
  };
});

/** 节点图标组件 */
const iconComponent = computed(() => {
  const iconMap: Record<string, any> = {
    START: PlayCircleOutlined,
    END: StopOutlined,
    LLM: RobotOutlined,
    KNOWLEDGE_RETRIEVAL: BookOutlined,
    QUESTION_CLASSIFIER: BranchesOutlined,
    PARAMETER_EXTRACTOR: ApiOutlined,
    AGENT: UserOutlined,
    IF_ELSE: BranchesOutlined,
    ITERATION: SyncOutlined,
    VARIABLE_AGGREGATOR: ApartmentOutlined,
    LOOP: SyncOutlined,
    PARALLEL: ApartmentOutlined,
    CODE: CodeOutlined,
    TEMPLATE: FileTextOutlined,
    DOC_EXTRACTOR: BookOutlined,
    LIST_OPERATOR: DatabaseOutlined,
    HTTP_REQUEST: ApiOutlined,
    TOOL: ToolOutlined,
    VARIABLE_ASSIGNER: DatabaseOutlined,
  };
  return iconMap[nodeType.value];
});

/** 图标文字（无图标时显示） */
const iconText = computed(() => {
  return nodeType.value.charAt(0);
});

/** 是否有输出 */
const hasOutput = computed(() => {
  return !['END'].includes(nodeType.value);
});

/** 是否为分支节点 */
const hasBranches = computed(() => {
  return ['IF_ELSE', 'QUESTION_CLASSIFIER'].includes(nodeType.value);
});

/** IF_ELSE 分支列表 */
const branches = computed(() => {
  if (nodeType.value === 'IF_ELSE') {
    return (
      config.value.branches || [
        { id: 'if-branch', type: 'IF', label: 'IF' },
        { id: 'else-branch', type: 'ELSE', label: 'ELSE' },
      ]
    );
  }
  return [];
});

/** QUESTION_CLASSIFIER 分类列表 */
const categories = computed(() => {
  if (nodeType.value === 'QUESTION_CLASSIFIER') {
    return config.value.categories || [];
  }
  return [];
});

/** 统一的分支/分类列表（用于 Handle 渲染） */
const allBranches = computed(() => {
  if (nodeType.value === 'IF_ELSE') {
    return branches.value;
  }
  if (nodeType.value === 'QUESTION_CLASSIFIER') {
    return categories.value;
  }
  return [];
});

/** 开始节点输入摘要 */
const startInputSummary = computed(() => {
  const fields = config.value.fields || [];
  return fields.length > 0 ? `${fields.length} 个输入` : '无输入';
});

/** 结束节点输出列表 */
const endOutputs = computed(() => {
  return config.value.outputs || [];
});

/** LLM 模型名称 */
const llmModelName = computed(() => {
  return config.value.modelId || '未配置';
});

/** 输入摘要 */
const inputSummary = computed(() => {
  switch (nodeType.value) {
    case 'IF_ELSE': {
      const conditions =
        config.value.branches?.reduce(
          (sum: number, b: any) => sum + (b.conditions?.length || 0),
          0,
        ) || 0;
      return `${conditions} 个条件`;
    }
    case 'LLM': {
      return config.value.modelId ? '已配置模型' : '未配置';
    }
    case 'START': {
      const fields = config.value.fields || [];
      return fields.length > 0 ? `${fields.length} 个输入` : '无输入';
    }
    default: {
      return '点击配置';
    }
  }
});

/** 输出摘要 */
const outputSummary = computed(() => {
  switch (nodeType.value) {
    case 'CODE': {
      return '代码执行结果';
    }
    case 'HTTP_REQUEST': {
      return 'HTTP 响应';
    }
    case 'LLM': {
      return config.value.outputVariable || 'output';
    }
    default: {
      return '变量输出';
    }
  }
});

// ==================== 方法 ====================

/**
 * 获取分支类名
 */
function getBranchClass(type: string) {
  return `branch-${type.toLowerCase()}`;
}

/**
 * 获取分支标签
 */
function getBranchLabel(type: string) {
  const map: Record<string, string> = {
    IF: '如果',
    ELIF: '否则如果',
    ELSE: '否则',
  };
  return map[type] || type;
}

/**
 * 获取分支条件摘要（用于节点卡片显示）
 */
function getBranchConditionSummary(branch: any) {
  if (branch.type === 'ELSE') return '默认分支';
  const conditions = branch.conditions || [];
  if (conditions.length === 0) return '未配置';
  return `${conditions.length} 个条件`;
}

/**
 * 获取分支 Handle Y 坐标（相对于节点顶部）
 * 头部高度约 40px + padding 4px，每个分支行高度约 28px
 */
function getBranchHandleY(index: number): number {
  const headerHeight = 44; // 头部高度
  const rowHeight = 28; // 每行高度
  const rowPadding = 6; // 行内 padding
  return headerHeight + index * rowHeight + rowPadding + rowHeight / 2;
}
</script>

<template>
  <div
    class="workflow-node"
    :class="[
      `node-${nodeType.toLowerCase()}`,
      {
        'is-selected': selected,
        'has-branches': hasBranches,
      },
    ]"
    :data-node-id="id"
    :data-node-type="nodeType"
    :style="nodeStyle"
  >
    <!-- 节点头部 -->
    <div class="node-header">
      <div class="node-icon">
        <component :is="iconComponent" v-if="iconComponent" />
        <span v-else class="icon-text">{{ iconText }}</span>
      </div>
      <div class="node-title">{{ data.label || nodeType }}</div>
    </div>

    <!-- 节点内容 - 非分支节点 -->
    <div v-if="!hasBranches" class="node-body">
      <template v-if="nodeType === 'START'">
        <div class="config-row">
          <span class="config-label">输入</span>
          <span class="config-value">{{ startInputSummary }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">输出</span>
          <span class="config-value">变量输出</span>
        </div>
      </template>
      <template v-else-if="nodeType === 'END'">
        <div class="config-row">
          <span class="config-label">输出</span>
          <span class="config-value config-tags">
            <span
              v-for="output in endOutputs"
              :key="output.name"
              class="output-tag"
            >
              {{ output.name }}
            </span>
            <span v-if="endOutputs.length === 0" class="empty-hint"
              >点击配置</span
            >
          </span>
        </div>
        <div class="config-row">
          <span class="config-label">输出类型</span>
          <span class="config-value">返回变量</span>
        </div>
      </template>
      <template v-else-if="nodeType === 'LLM'">
        <div class="config-row">
          <span class="config-label">模型</span>
          <span class="config-value">{{ llmModelName }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">输出</span>
          <span class="config-value">{{
            config.outputVariable || 'output'
          }}</span>
        </div>
      </template>
      <template v-else>
        <div class="config-row">
          <span class="config-label">输入</span>
          <span class="config-value">{{ inputSummary }}</span>
        </div>
        <div v-if="hasOutput" class="config-row">
          <span class="config-label">输出</span>
          <span class="config-value">{{ outputSummary }}</span>
        </div>
      </template>
    </div>

    <!-- 分支列表 (IF_ELSE) -->
    <div v-else-if="nodeType === 'IF_ELSE'" class="branch-outputs">
      <div
        v-for="branch in branches"
        :key="branch.id"
        class="branch-row"
        :class="getBranchClass(branch.type)"
      >
        <span class="branch-label">{{ getBranchLabel(branch.type) }}</span>
        <span class="branch-condition">{{
          getBranchConditionSummary(branch)
        }}</span>
      </div>
    </div>

    <!-- 分类列表 (QUESTION_CLASSIFIER) -->
    <div v-else-if="nodeType === 'QUESTION_CLASSIFIER'" class="branch-outputs">
      <div
        v-for="(category, index) in categories"
        :key="category.id"
        class="branch-row branch-category"
      >
        <span class="branch-label">类别 {{ Number(index) + 1 }}</span>
        <span class="branch-condition">{{ category.name || '未命名' }}</span>
      </div>
      <div v-if="categories.length === 0" class="branch-row branch-empty">
        <span class="branch-condition">点击配置分类</span>
      </div>
    </div>

    <!-- 输入 Handle -->
    <Handle
      v-if="nodeType !== 'START'"
      id="input"
      type="target"
      :position="Position.Left"
      :connectable="props.connectable"
      data-testid="workflow-node-input-handle"
      class="input-handle"
    />

    <!-- 输出 Handle (非分支节点) -->
    <Handle
      v-if="nodeType !== 'END' && !hasBranches"
      id="output"
      type="source"
      :position="Position.Right"
      :connectable="props.connectable"
      data-testid="workflow-node-output-handle"
      class="output-handle"
    />

    <!-- 分支输出 Handles (放在节点根元素下，让 Vue Flow 正确计算位置) -->
    <Handle
      v-for="(branch, index) in allBranches"
      :key="`handle-${branch.id}`"
      :id="outputHandleForBranch(branch.id)"
      type="source"
      :position="Position.Right"
      :connectable="props.connectable"
      :style="{ top: `${getBranchHandleY(Number(index))}px` }"
      data-testid="workflow-node-branch-handle"
      class="branch-handle"
    />
  </div>
</template>

<style scoped lang="less">
.workflow-node {
  min-width: 200px;
  max-width: 280px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: all 0.2s ease;
  position: relative;

  &.is-selected {
    border-color: #1890ff;
    box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
  }

  &:hover {
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  }
}

.node-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: linear-gradient(
    135deg,
    var(--node-bg, #f0f5ff) 0%,
    var(--node-bg-light, #fff) 100%
  );
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);

  .node-icon {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    color: var(--node-border);

    .icon-text {
      font-weight: 600;
      font-size: 14px;
    }
  }

  .node-title {
    flex: 1;
    font-size: 14px;
    font-weight: 500;
    color: #262626;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.node-body {
  padding: 8px 12px;

  .config-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;

    .config-label {
      font-size: 12px;
      color: #8c8c8c;
      min-width: 50px;
    }

    .config-value {
      font-size: 12px;
      color: #595959;
      max-width: 160px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;

      &.config-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        max-width: none;

        .output-tag {
          display: inline-flex;
          align-items: center;
          gap: 2px;
          padding: 2px 8px;
          font-size: 11px;
          color: #1890ff;
          background: #e6f7ff;
          border-radius: 4px;

          .tag-icon {
            font-size: 10px;
          }
        }

        .empty-hint {
          color: #bfbfbf;
          font-style: italic;
        }
      }
    }
  }
}

.branch-outputs {
  padding: 4px 0;

  .branch-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    position: relative;

    &.branch-if {
      border-left: 3px solid #1890ff;
      background: rgba(24, 144, 255, 0.05);
    }

    &.branch-elif {
      border-left: 3px solid #fa8c16;
      background: rgba(250, 140, 22, 0.05);
    }

    &.branch-else {
      border-left: 3px solid #52c41a;
      background: rgba(82, 196, 26, 0.05);
    }

    &.branch-category {
      border-left: 3px solid #722ed1;
      background: rgba(114, 46, 209, 0.05);
    }

    &.branch-empty {
      border-left: 3px solid #d9d9d9;
      background: rgba(0, 0, 0, 0.02);
      font-style: italic;
    }

    .branch-label {
      font-size: 12px;
      font-weight: 500;
      color: #595959;
      min-width: 56px;
    }

    .branch-condition {
      flex: 1;
      font-size: 11px;
      color: #8c8c8c;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

// Handle 样式 - 悬停放大并显示+号效果
:deep(.vue-flow__handle) {
  width: 12px;
  height: 12px;
  background: #fff;
  border: 2px solid #d9d9d9;
  transition: all 0.2s ease;

  &::after {
    content: '+';
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 0;
    font-weight: bold;
    color: #fff;
    opacity: 0;
    transition: all 0.2s ease;
  }

  &:hover {
    width: 20px;
    height: 20px;
    background: #1890ff;
    border-color: #1890ff;
    cursor: crosshair;

    &::after {
      font-size: 14px;
      opacity: 1;
    }
  }
}

.input-handle {
  left: -6px;
}

.output-handle {
  right: -6px;
}

.branch-handle {
  right: -6px;
  // top 由 style 属性动态设置
}
</style>
