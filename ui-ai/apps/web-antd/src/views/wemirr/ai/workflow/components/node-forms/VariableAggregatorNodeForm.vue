<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 聚合组列表 -->
    <div class="groups-section">
      <div class="section-header">
        <span class="section-title">聚合组配置</span>
        <a-button type="primary" size="small" ghost @click="addGroup">
          <PlusOutlined /> 添加聚合组
        </a-button>
      </div>

      <!-- BUG8：多个聚合组输出同名时页面即时提示，不用等到运行才发现 -->
      <a-alert
        v-if="duplicateNames.length > 0"
        type="error"
        show-icon
        class="duplicate-alert"
        message="聚合输出变量名重复"
        :description="`以下变量名被多个聚合组使用：${duplicateNames.join('、')}，下游只会引用到最后一个组的值`"
      />

      <draggable
        v-model="formData.groups"
        item-key="id"
        handle=".drag-handle"
        @change="handleChange"
      >
        <template #item="{ element: group, index }">
          <div class="group-item">
            <div class="group-header">
              <HolderOutlined class="drag-handle" />
              <span class="group-index">聚合组 {{ index + 1 }}</span>
              <a-button
                type="text"
                danger
                size="small"
                :disabled="formData.groups.length <= 1"
                @click="removeGroup(index)"
              >
                <DeleteOutlined />
              </a-button>
            </div>

            <div class="group-content">
              <!-- 输出变量名 -->
              <a-form-item label="输出变量名" class="compact-form-item">
                <a-input
                  v-model:value="group.outputVariable"
                  placeholder="aggregatedResult"
                  size="small"
                  :status="isDuplicateGroup(group) ? 'error' : undefined"
                  @change="handleChange"
                />
                <div v-if="isDuplicateGroup(group)" class="field-error">
                  输出变量名重复，请为每个聚合组设置唯一名称
                </div>
              </a-form-item>

              <!-- 变量类型约束 -->
              <a-form-item label="变量类型" class="compact-form-item">
                <a-select
                  v-model:value="group.variableType"
                  size="small"
                  @change="handleChange"
                >
                  <a-select-option value="any">任意类型</a-select-option>
                  <a-select-option value="string">字符串</a-select-option>
                  <a-select-option value="number">数字</a-select-option>
                  <a-select-option value="boolean">布尔值</a-select-option>
                  <a-select-option value="array">数组</a-select-option>
                  <a-select-option value="object">对象</a-select-option>
                </a-select>
              </a-form-item>

              <!-- 聚合策略 -->
              <a-form-item label="聚合策略" class="compact-form-item">
                <a-select
                  v-model:value="group.strategy"
                  size="small"
                  @change="handleChange"
                >
                  <a-select-option value="FIRST_NON_NULL">
                    第一个非空值
                  </a-select-option>
                  <a-select-option value="LAST_NON_NULL">
                    最后一个非空值
                  </a-select-option>
                  <a-select-option value="MERGE_TO_ARRAY">
                    合并为数组
                  </a-select-option>
                  <a-select-option value="MERGE_OBJECTS">
                    合并对象（仅对象类型）
                  </a-select-option>
                </a-select>
                <div class="strategy-hint">
                  {{ getStrategyHint(group.strategy) }}
                </div>
              </a-form-item>

              <!-- 源变量列表 -->
              <a-form-item label="源变量" class="compact-form-item">
                <div class="source-variables">
                  <div
                    v-for="(sourceVar, varIndex) in group.sourceVariables"
                    :key="`${varIndex}-${sourceVar}`"
                    class="source-var-row"
                  >
                    <VariableInput
                      v-model="group.sourceVariables[varIndex]"
                      :current-node-id="nodeId"
                      :placeholder="'{{nodeName.variable}}'"
                      class="source-var-input"
                      @change="handleChange"
                    />
                    <a-button
                      type="text"
                      danger
                      size="small"
                      :disabled="group.sourceVariables.length <= 1"
                      @click="removeSourceVariable(group, varIndex)"
                    >
                      <MinusCircleOutlined />
                    </a-button>
                  </div>
                  <a-button
                    type="dashed"
                    size="small"
                    block
                    @click="addSourceVariable(group)"
                  >
                    <PlusOutlined /> 添加源变量
                  </a-button>
                </div>
                <div class="form-hint">
                  添加来自不同分支的变量引用，格式:
                  <code v-pre>{{ nodeName.variable }}</code>
                </div>
              </a-form-item>
            </div>
          </div>
        </template>
      </draggable>

      <div
        v-if="!formData.groups || formData.groups.length === 0"
        class="empty-groups"
      >
        <InboxOutlined class="empty-icon" />
        <span>暂无聚合组，点击上方按钮添加</span>
      </div>
    </div>

    <!-- 使用说明 -->
    <a-alert type="info" show-icon class="usage-info">
      <template #message>使用说明</template>
      <template #description>
        <ul class="help-list">
          <li>变量聚合器用于合并来自不同分支的输出变量</li>
          <li>通常放置在条件分支（IF/ELSE）或并行分支之后</li>
          <li>
            源变量使用 <code v-pre>{{ nodeName.variable }}</code> 格式引用
          </li>
          <li>确保聚合的变量类型一致，避免类型冲突</li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * 变量聚合器节点配置表单
 * 合并多分支输出变量
 */
import type {
  AggregationGroup,
  AggregationStrategy,
  AggregatorVariableType,
  VariableAggregatorConfig,
} from '#/api/ai-workflow/types';

import {
  DeleteOutlined,
  HolderOutlined,
  InboxOutlined,
  MinusCircleOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { computed, reactive, watch } from 'vue';
import draggable from 'vuedraggable';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: VariableAggregatorConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: VariableAggregatorConfig): void;
}>();

// 生成唯一ID（拖拽/渲染的稳定 key；输出变量名可能为空或重名，不能当 key）
function generateId(): string {
  return `group_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
}

// 创建默认聚合组
function createDefaultGroup(): AggregationGroup {
  return {
    id: generateId(),
    outputVariable: '',
    sourceVariables: [''],
    variableType: 'any' as AggregatorVariableType,
    strategy: 'FIRST_NON_NULL' as AggregationStrategy,
  };
}

type VariableAggregatorFormData = {
  groups: AggregationGroup[];
};

// 表单数据
const formData = reactive<VariableAggregatorFormData>({
  groups: [createDefaultGroup()],
});

/** 补全稳定 key 与至少一行的源变量占位，保证空行也能渲染出来 */
function normalizeGroups(
  groups: AggregationGroup[] | undefined,
): AggregationGroup[] {
  if (!groups || groups.length === 0) {
    return [createDefaultGroup()];
  }
  return groups.map((g) => ({
    ...g,
    id: g.id || generateId(),
    sourceVariables: g.sourceVariables?.length ? [...g.sourceVariables] : [''],
  }));
}

/**
 * 本地刚 emit 出去的配置快照（nodeId + 组内容）。
 * BUG8：父层（PropertyPanel / node.data）会把这份配置原样回传，若不加守卫，
 * 回传值会立即重建 formData，把用户刚点出来的空聚合组/空源变量行覆盖掉，
 * 表现为「点击添加聚合组、添加源变量毫无反应」。
 */
let emittedSignature = '';

function signatureOf(groups: AggregationGroup[] | undefined): string {
  return JSON.stringify(
    (groups || []).map((g) => [
      g.outputVariable || '',
      g.variableType || '',
      g.strategy || '',
      g.sourceVariables || [],
    ]),
  );
}

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    const groups = config?.groups;
    if (`${props.nodeId}|${signatureOf(groups)}` === emittedSignature) {
      // 自己刚 emit 的内容，保留本地编辑状态（含未填写的空行）
      return;
    }
    emittedSignature = '';
    formData.groups = normalizeGroups(groups);
  },
  { immediate: true, deep: true },
);

/** 输出变量名重复的变量名列表（忽略空值，空值由必填语义另行提示） */
const duplicateNames = computed<string[]>(() => {
  const counts = new Map<string, number>();
  for (const group of formData.groups || []) {
    const name = (group.outputVariable || '').trim();
    if (!name) {
      continue;
    }
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts].filter(([, count]) => count > 1).map(([name]) => name);
});

function isDuplicateGroup(group: AggregationGroup): boolean {
  const name = (group.outputVariable || '').trim();
  return !!name && duplicateNames.value.includes(name);
}

// 获取策略提示
function getStrategyHint(strategy?: AggregationStrategy): string {
  switch (strategy) {
    case 'FIRST_NON_NULL':
      return '返回第一个非空的源变量值';
    case 'LAST_NON_NULL':
      return '返回最后一个非空的源变量值';
    case 'MERGE_TO_ARRAY':
      return '将所有源变量值合并为数组';
    case 'MERGE_OBJECTS':
      return '将多个对象合并为一个（后者覆盖前者）';
    default:
      return '';
  }
}

// 添加聚合组
function addGroup() {
  formData.groups.push(createDefaultGroup());
  handleChange();
}

// 移除聚合组
function removeGroup(index: number) {
  if (formData.groups.length > 1) {
    formData.groups.splice(index, 1);
    handleChange();
  }
}

// 添加源变量
function addSourceVariable(group: AggregationGroup) {
  group.sourceVariables = group.sourceVariables || [];
  group.sourceVariables.push('');
  handleChange();
}

// 移除源变量
function removeSourceVariable(group: AggregationGroup, index: number) {
  if (group.sourceVariables && group.sourceVariables.length > 1) {
    group.sourceVariables.splice(index, 1);
    handleChange();
  }
}

// 处理配置变更
function handleChange() {
  // BUG8：不再过滤 outputVariable 为空的组与为空的源变量行，
  // 用户加出来的空行属于待填写状态，过滤掉会让新增瞬间消失
  const config: VariableAggregatorConfig = {
    groups: (formData.groups || []).map((g) => ({
      ...g,
      sourceVariables: [...(g.sourceVariables || [])],
    })),
  };
  emittedSignature = `${props.nodeId}|${signatureOf(config.groups)}`;
  emit('update:config', config);
}
</script>

<style scoped lang="less">
.node-form {
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

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .groups-section {
    .duplicate-alert {
      margin-bottom: 12px;
    }

    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;

      .section-title {
        font-size: 13px;
        font-weight: 500;
        color: #262626;
      }
    }

    .group-item {
      margin-bottom: 16px;
      padding: 12px;
      background-color: #fafafa;
      border: 1px solid #f0f0f0;
      border-radius: 6px;
      border-left: 3px solid #9254de;

      .group-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 12px;

        .drag-handle {
          cursor: move;
          color: #bfbfbf;

          &:hover {
            color: #1890ff;
          }
        }

        .group-index {
          flex: 1;
          font-size: 12px;
          font-weight: 500;
          color: #595959;
        }
      }

      .group-content {
        .compact-form-item {
          margin-bottom: 12px;

          :deep(.ant-form-item-label) {
            padding-bottom: 2px;
          }
        }

        .field-error {
          margin-top: 4px;
          font-size: 11px;
          color: #ff4d4f;
        }

        .strategy-hint {
          margin-top: 4px;
          font-size: 11px;
          color: #8c8c8c;
        }

        .source-variables {
          display: flex;
          flex-direction: column;
          gap: 8px;

          .source-var-row {
            display: flex;
            align-items: center;
            gap: 8px;

            .source-var-input {
              flex: 1;
            }
          }
        }
      }
    }

    .empty-groups {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      padding: 24px;
      color: #8c8c8c;
      background-color: #fafafa;
      border: 1px dashed #d9d9d9;
      border-radius: 6px;

      .empty-icon {
        font-size: 32px;
        color: #d9d9d9;
      }
    }
  }

  .usage-info {
    margin-top: 16px;

    .help-list {
      padding-left: 16px;
      margin: 0;
      font-size: 12px;

      li {
        margin-bottom: 4px;

        &:last-child {
          margin-bottom: 0;
        }
      }

      code {
        padding: 1px 4px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 2px;
      }
    }
  }
}
</style>
