<script setup lang="ts">
/**
 * 变量检查器组件 (Coze-Style)
 * 按节点分组显示变量，支持搜索过滤、JSON 展示、变量编辑
 *
 */

import type { VariableGroup, VariableItem } from '#/store/debug-store';

import { computed, ref, watch } from 'vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';

import {
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  FormatPainterOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Collapse,
  CollapsePanel,
  Empty,
  Input,
  Modal,
  Spin,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { useVariableEditor } from './use-variable-editor';

// ==================== Props ====================

interface Props {
  /** 变量分组列表 */
  groups: VariableGroup[];
  /** 是否处于暂停状态（暂停时允许编辑） */
  isPaused?: boolean;
  /** 执行ID（用于调用后端 API 更新变量） */
  executionId?: null | string;
}

const props = withDefaults(defineProps<Props>(), {
  isPaused: false,
  executionId: null,
});

// ==================== Emits ====================

const emit = defineEmits<{
  /** 变量值更新事件（本地更新，用于同步 store） */
  (e: 'update', nodeId: string, variableName: string, newValue: any): void;
}>();

// ==================== 变量编辑器 Hook ====================

const variableEditor = useVariableEditor(() => props.executionId);

// ==================== 状态 ====================

/** 搜索文本 */
const searchText = ref('');

/** 展开的节点 */
const expandedNodes = ref<string[]>([]);

/** 最近更新的变量（5秒内） */
const recentlyUpdatedThreshold = 5000;

// ==================== 计算属性 ====================

/** 过滤后的变量分组 */
const filteredGroups = computed(() => {
  if (!searchText.value.trim()) {
    return props.groups;
  }

  const search = searchText.value.toLowerCase().trim();

  return props.groups
    .map((group) => ({
      ...group,
      variables: group.variables.filter((variable) =>
        variable.name.toLowerCase().includes(search),
      ),
    }))
    .filter((group) => group.variables.length > 0);
});

/** 是否有数据 */
const hasData = computed(() => props.groups.length > 0);

/** 变量总数 */
const totalVariableCount = computed(() => {
  return props.groups.reduce((sum, group) => sum + group.variables.length, 0);
});

/** 过滤后的变量总数 */
const filteredVariableCount = computed(() => {
  return filteredGroups.value.reduce(
    (sum, group) => sum + group.variables.length,
    0,
  );
});

// ==================== 方法 ====================

/**
 * 检查变量是否最近更新
 */
function isRecentlyUpdated(variable: VariableItem): boolean {
  if (!variable.updatedAt) return false;
  const now = Date.now();
  const updatedTime = new Date(variable.updatedAt).getTime();
  return now - updatedTime < recentlyUpdatedThreshold;
}

/**
 * 获取变量类型标签颜色
 */
function getTypeColor(type: string): string {
  const colorMap: Record<string, string> = {
    string: 'green',
    number: 'blue',
    boolean: 'orange',
    object: 'purple',
    array: 'cyan',
    undefined: 'default',
    null: 'default',
  };
  return colorMap[type] || 'default';
}

/**
 * 格式化变量类型显示
 */
function formatType(value: any): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

/**
 * 开始编辑变量
 */
function startEdit(nodeId: string, nodeName: string, variable: VariableItem) {
  variableEditor.startEdit(nodeId, nodeName, variable.name, variable.value);
}

/**
 * 保存变量编辑
 */
async function saveVariable() {
  const success = await variableEditor.saveEdit();
  if (success && variableEditor.editState.value) {
    // 同步更新到本地 store
    const validation = variableEditor.validateJson(
      variableEditor.editValue.value,
    );
    if (validation.valid) {
      emit(
        'update',
        variableEditor.editState.value.nodeId,
        variableEditor.editState.value.variableName,
        validation.value,
      );
    }
  }
}

/**
 * 取消编辑
 */
function cancelEdit() {
  variableEditor.cancelEdit();
}

/**
 * 清除搜索
 */
function clearSearch() {
  searchText.value = '';
}

/**
 * 展开所有节点
 */
function expandAll() {
  expandedNodes.value = props.groups.map((g) => g.nodeId);
}

/**
 * 折叠所有节点
 */
function collapseAll() {
  expandedNodes.value = [];
}

// ==================== 监听 ====================

// 监听编辑值变化，实时验证 JSON
watch(
  () => variableEditor.editValue.value,
  (newValue) => {
    if (newValue) {
      variableEditor.updateEditValue(newValue);
    }
  },
);

// 当有新的分组时，自动展开
watch(
  () => props.groups,
  (newGroups) => {
    if (newGroups.length > 0 && expandedNodes.value.length === 0) {
      // 默认展开第一个分组
      expandedNodes.value = [newGroups[0]!.nodeId];
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="variable-inspector">
    <!-- 工具栏 -->
    <div class="inspector-toolbar">
      <!-- 搜索框 -->
      <Input
        v-model:value="searchText"
        placeholder="搜索变量名..."
        allow-clear
        class="search-input"
        @clear="clearSearch"
      >
        <template #prefix>
          <SearchOutlined />
        </template>
      </Input>

      <!-- 操作按钮 -->
      <div class="toolbar-actions">
        <Tooltip title="展开全部">
          <Button size="small" @click="expandAll">展开</Button>
        </Tooltip>
        <Tooltip title="折叠全部">
          <Button size="small" @click="collapseAll">折叠</Button>
        </Tooltip>
      </div>
    </div>

    <!-- 统计信息 -->
    <div v-if="hasData" class="inspector-stats">
      <span v-if="searchText">
        找到 {{ filteredVariableCount }} / {{ totalVariableCount }} 个变量
      </span>
      <span v-else>
        共 {{ totalVariableCount }} 个变量，{{ groups.length }} 个节点
      </span>
    </div>

    <!-- 空状态 -->
    <Empty
      v-if="!hasData"
      description="暂无变量数据"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />

    <!-- 搜索无结果 -->
    <Empty
      v-else-if="filteredGroups.length === 0"
      description="未找到匹配的变量"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    >
      <Button type="link" @click="clearSearch">清除搜索</Button>
    </Empty>

    <!-- 变量分组列表 -->
    <Collapse
      v-else
      v-model:active-key="expandedNodes"
      class="variable-collapse"
    >
      <CollapsePanel
        v-for="group in filteredGroups"
        :key="group.nodeId"
        :header="group.nodeName"
      >
        <template #extra>
          <Tag color="blue" size="small">{{ group.variables.length }}</Tag>
        </template>

        <div class="variable-list">
          <div
            v-for="variable in group.variables"
            :key="variable.name"
            class="variable-item"
            :class="{ 'recently-updated': isRecentlyUpdated(variable) }"
          >
            <!-- 变量头部 -->
            <div class="variable-header">
              <span class="variable-name">{{ variable.name }}</span>
              <Tag
                :color="getTypeColor(formatType(variable.value))"
                size="small"
              >
                {{ formatType(variable.value) }}
              </Tag>
              <Tooltip v-if="isPaused" title="编辑变量">
                <Button
                  type="link"
                  size="small"
                  class="edit-btn"
                  @click.stop="
                    startEdit(group.nodeId, group.nodeName, variable)
                  "
                >
                  <EditOutlined />
                </Button>
              </Tooltip>
            </div>

            <!-- 变量值 -->
            <div class="variable-value">
              <!-- 简单类型直接显示 -->
              <template
                v-if="
                  ['string', 'number', 'boolean'].includes(
                    formatType(variable.value),
                  )
                "
              >
                <span
                  class="simple-value"
                  :class="`type-${formatType(variable.value)}`"
                >
                  {{
                    variable.value === ''
                      ? '(空字符串)'
                      : String(variable.value)
                  }}
                </span>
              </template>

              <!-- null/undefined -->
              <template
                v-else-if="
                  variable.value === null || variable.value === undefined
                "
              >
                <span class="null-value">{{
                  variable.value === null ? 'null' : 'undefined'
                }}</span>
              </template>

              <!-- 复杂类型使用 JSON 展示 -->
              <template v-else>
                <VueJsonPretty
                  :data="variable.value"
                  :deep="2"
                  :show-length="true"
                  :show-line="false"
                />
              </template>
            </div>

            <!-- 更新时间 -->
            <div v-if="variable.updatedAt" class="variable-meta">
              <span class="update-time">
                更新于 {{ new Date(variable.updatedAt).toLocaleTimeString() }}
              </span>
            </div>
          </div>
        </div>
      </CollapsePanel>
    </Collapse>

    <!-- 编辑变量对话框 -->
    <Modal
      v-model:open="variableEditor.isEditing.value"
      title="编辑变量"
      :width="600"
      :confirm-loading="variableEditor.isSaving.value"
      @cancel="cancelEdit"
    >
      <template #footer>
        <div class="modal-footer">
          <div class="footer-left">
            <Tooltip title="格式化 JSON">
              <Button size="small" @click="variableEditor.formatJson">
                <FormatPainterOutlined />
              </Button>
            </Tooltip>
            <Tooltip title="重置为原始值">
              <Button size="small" @click="variableEditor.resetToOriginal">
                <ReloadOutlined />
              </Button>
            </Tooltip>
          </div>
          <div class="footer-right">
            <Button @click="cancelEdit"> <CloseOutlined /> 取消 </Button>
            <Button
              type="primary"
              :disabled="!!variableEditor.validationError.value"
              :loading="variableEditor.isSaving.value"
              @click="saveVariable"
            >
              <CheckOutlined /> 保存
            </Button>
          </div>
        </div>
      </template>

      <Spin :spinning="variableEditor.isSaving.value">
        <div v-if="variableEditor.editState.value" class="edit-modal-content">
          <div class="edit-info">
            <span class="edit-label">节点:</span>
            <span>{{ variableEditor.editState.value.nodeName }}</span>
          </div>
          <div class="edit-info">
            <span class="edit-label">变量名:</span>
            <span class="edit-variable-name">{{
              variableEditor.editState.value.variableName
            }}</span>
          </div>

          <div class="edit-value-section">
            <div class="edit-value-label">变量值 (JSON 格式):</div>
            <Input.TextArea
              v-model:value="variableEditor.editValue.value"
              :rows="12"
              :status="variableEditor.validationError.value ? 'error' : ''"
              class="edit-textarea"
              placeholder="请输入有效的 JSON 值"
              @change="
                (e: any) => variableEditor.updateEditValue(e.target.value)
              "
            />
            <div v-if="variableEditor.validationError.value" class="json-error">
              <span class="error-icon">⚠</span>
              {{ variableEditor.validationError.value }}
            </div>
          </div>
        </div>
      </Spin>
    </Modal>
  </div>
</template>

<style lang="less" scoped>
.variable-inspector {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background-color: var(--ant-color-bg-container);

  .inspector-toolbar {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 12px;
    border-bottom: 1px solid var(--ant-color-border);

    .search-input {
      flex: 1;
    }

    .toolbar-actions {
      display: flex;
      gap: 4px;
    }
  }

  .inspector-stats {
    padding: 8px 12px;
    color: var(--ant-color-text-secondary);
    font-size: 12px;
    background-color: var(--ant-color-bg-layout);
  }

  .variable-collapse {
    flex: 1;
    overflow-y: auto;

    :deep(.ant-collapse-header) {
      font-weight: 500;
    }

    :deep(.ant-collapse-content-box) {
      padding: 0 !important;
    }
  }

  .variable-list {
    .variable-item {
      padding: 12px 16px;
      border-bottom: 1px solid var(--ant-color-border-secondary);
      transition: background-color 0.3s ease;

      &:last-child {
        border-bottom: none;
      }

      &:hover {
        background-color: var(--ant-color-bg-layout);
      }

      &.recently-updated {
        background-color: rgba(82, 196, 26, 0.1);
        animation: highlight-fade 5s ease-out;
      }

      .variable-header {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 8px;

        .variable-name {
          font-weight: 500;
          font-family: 'Fira Code', monospace;
        }

        .edit-btn {
          padding: 0 4px;
          opacity: 0;
          transition: opacity 0.2s;
        }
      }

      &:hover .edit-btn {
        opacity: 1;
      }

      .variable-value {
        padding: 8px;
        background-color: var(--ant-color-bg-layout);
        border-radius: 4px;

        .simple-value {
          font-family: 'Fira Code', monospace;
          font-size: 13px;
          word-break: break-all;

          &.type-string {
            color: #52c41a;
          }

          &.type-number {
            color: #1890ff;
          }

          &.type-boolean {
            color: #fa8c16;
          }
        }

        .null-value {
          color: var(--ant-color-text-quaternary);
          font-style: italic;
        }

        :deep(.vjs-tree) {
          font-size: 12px;
        }
      }

      .variable-meta {
        margin-top: 4px;

        .update-time {
          color: var(--ant-color-text-quaternary);
          font-size: 11px;
        }
      }
    }
  }
}

// 编辑模态框样式
.edit-modal-content {
  .edit-info {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;

    .edit-label {
      color: var(--ant-color-text-secondary);
      min-width: 50px;
    }

    .edit-variable-name {
      font-weight: 500;
      font-family: 'Fira Code', monospace;
    }
  }

  .edit-value-section {
    margin-top: 16px;

    .edit-value-label {
      margin-bottom: 8px;
      color: var(--ant-color-text-secondary);
    }

    .edit-textarea {
      font-family: 'Fira Code', monospace;
      font-size: 13px;
    }

    .json-error {
      display: flex;
      gap: 4px;
      align-items: center;
      margin-top: 8px;
      padding: 8px 12px;
      color: var(--ant-color-error);
      font-size: 12px;
      background-color: var(--ant-color-error-bg);
      border-radius: 4px;

      .error-icon {
        font-size: 14px;
      }
    }
  }
}

// 模态框底部样式
.modal-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;

  .footer-left {
    display: flex;
    gap: 8px;
  }

  .footer-right {
    display: flex;
    gap: 8px;
  }
}

// 高亮动画
@keyframes highlight-fade {
  0% {
    background-color: rgba(82, 196, 26, 0.2);
  }
  100% {
    background-color: transparent;
  }
}

// 暗色模式适配
html[class='dark'] {
  .variable-inspector {
    .variable-list {
      .variable-item {
        &:hover {
          background-color: var(--ant-color-bg-elevated);
        }

        &.recently-updated {
          background-color: rgba(82, 196, 26, 0.15);
        }

        .variable-value {
          background-color: var(--ant-color-bg-elevated);
        }
      }
    }
  }
}
</style>
