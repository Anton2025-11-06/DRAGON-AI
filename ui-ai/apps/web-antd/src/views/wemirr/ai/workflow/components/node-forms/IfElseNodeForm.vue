<script setup lang="ts">
/**
 * IF/ELSE 条件分支节点配置表单 (Coze 风格)
 * 支持 IF/ELIF/ELSE 多分支，右值多态（字面量/变量引用）
 */
import type {
  BranchType,
  CompareOperator,
  Condition,
  ConditionBranch,
  IfElseNodeConfig,
  LogicalOperator,
} from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import {
  CloseOutlined,
  DownOutlined,
  MoreOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: IfElseNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: IfElseNodeConfig): void;
}>();

// 生成唯一ID
function generateId(): string {
  return `branch_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
}

// 创建默认条件
function createDefaultCondition(): Condition {
  return {
    variable: '',
    operator: 'EQUALS' as CompareOperator,
    value: '',
    valueIsVariable: false,
  };
}

// 创建默认分支
function createDefaultBranch(type: BranchType, label: string): ConditionBranch {
  return {
    id: generateId(),
    label,
    type,
    conditions: type === 'ELSE' ? undefined : [createDefaultCondition()],
    operator: type === 'ELSE' ? undefined : ('AND' as LogicalOperator),
  };
}

// 默认分支配置
const defaultBranches: ConditionBranch[] = [
  createDefaultBranch('IF', 'IF 分支'),
  createDefaultBranch('ELSE', 'ELSE 分支'),
];

// 表单数据
const formData = reactive<IfElseNodeConfig>({
  branches: defaultBranches.map((b) => ({
    ...b,
    conditions: b.conditions?.map((c) => ({ ...c })),
  })),
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    formData.branches =
      config.branches && config.branches.length > 0
        ? config.branches.map((b) => ({
            ...b,
            conditions: b.conditions?.map((c) => ({ ...c })),
          }))
        : defaultBranches.map((b) => ({
            ...b,
            conditions: b.conditions?.map((c) => ({ ...c })),
          }));
  },
  { immediate: true, deep: true },
);

// 是否有 ELSE 分支
const hasElseBranch = computed(() =>
  formData.branches?.some((b) => b.type === 'ELSE'),
);

// 是否可以添加 ELIF 分支
const canAddElif = computed(() => {
  const branches = formData.branches || [];
  // 可以在 IF 和 ELSE 之间添加 ELIF
  return branches.length > 0;
});

// ELSE 分支占位符
const elsePlaceholder = ref('');

// 获取分支标题
function getBranchTitle(type: BranchType): string {
  switch (type) {
    case 'ELIF': {
      return '否则如果';
    }
    case 'ELSE': {
      return '否则';
    }
    case 'IF': {
      return '如果';
    }
    default: {
      return type;
    }
  }
}

// 切换右值类型（字面量/变量引用）
function switchValueType(condition: Condition, key: string) {
  condition.valueIsVariable = key === 'variable';
  condition.value = '';
  handleChange();
}

// 是否为空值检查运算符
function isNullCheckOperator(operator: CompareOperator): boolean {
  return ['IS_EMPTY', 'IS_NOT_EMPTY', 'IS_NOT_NULL', 'IS_NULL'].includes(
    operator,
  );
}

// 是否可以移除分支
function canRemoveBranch(type: BranchType): boolean {
  // IF 分支不能移除，ELIF 和 ELSE 可以移除
  return type !== 'IF';
}

// 添加 ELIF 分支
function addElifBranch() {
  const branches = formData.branches || [];
  const elifCount = branches.filter((b) => b.type === 'ELIF').length;
  const newBranch = createDefaultBranch('ELIF', `ELIF 分支 ${elifCount + 1}`);

  // 在 ELSE 分支之前插入
  const elseIndex = branches.findIndex((b) => b.type === 'ELSE');
  if (elseIndex === -1) {
    branches.push(newBranch);
  } else {
    branches.splice(elseIndex, 0, newBranch);
  }

  handleChange();
}

// 添加 ELSE 分支
function addElseBranch() {
  if (!hasElseBranch.value) {
    formData.branches = formData.branches || [];
    formData.branches.push(createDefaultBranch('ELSE', 'ELSE 分支'));
    handleChange();
  }
}

// 移除分支
function removeBranch(index: number) {
  const branch = formData.branches?.[index];
  if (branch && canRemoveBranch(branch.type)) {
    formData.branches?.splice(index, 1);
    handleChange();
  }
}

// 添加条件
function addCondition(branch: ConditionBranch) {
  branch.conditions = branch.conditions || [];
  branch.conditions.push(createDefaultCondition());
  handleChange();
}

// 移除条件
function removeCondition(branch: ConditionBranch, condIndex: number) {
  if (branch.conditions && branch.conditions.length > 1) {
    branch.conditions.splice(condIndex, 1);
    handleChange();
  }
}

// 处理配置变更
function handleChange() {
  const config: IfElseNodeConfig = {
    branches: formData.branches
      ?.map((b) => ({
        ...b,
        // 保留所有条件，不过滤空变量（允许用户逐步填写）
        conditions: b.conditions?.map((c) => ({ ...c })),
      }))
      .filter((b) => b.id),
  };
  emit('update:config', config);
}
</script>

<template>
  <div class="selector-node-form">
    <!-- 标题说明 -->
    <div class="form-header">
      <span class="header-desc">连接多个下游分支，若设定的条件成立则仅运行对应的分支，均不成立则只运行"否则"分支</span>
    </div>

    <!-- 条件分支标题 -->
    <div class="section-title">条件分支</div>

    <!-- 条件分支列表 -->
    <div class="branches-list">
      <div
        v-for="(branch, index) in formData.branches"
        :key="branch.id"
        class="branch-card"
        :class="{ 'branch-else': branch.type === 'ELSE' }"
      >
        <!-- 分支头部 -->
        <div class="branch-header">
          <div class="branch-title">
            <span class="branch-icon">:</span>
            <span class="branch-name">{{ getBranchTitle(branch.type) }}</span>
            <span v-if="branch.type !== 'ELSE'" class="branch-priority">优先级 {{ index + 1 }}</span>
          </div>
          <div class="branch-header-right">
            <!-- 分支内条件的逻辑关系选择器（且/或） -->
            <a-select
              v-if="
                branch.type !== 'ELSE' &&
                branch.conditions &&
                branch.conditions.length > 1
              "
              v-model:value="branch.operator"
              size="small"
              class="logic-select-header"
              @change="handleChange"
            >
              <a-select-option value="AND">且</a-select-option>
              <a-select-option value="OR">或</a-select-option>
            </a-select>
            <a-button
              v-if="canRemoveBranch(branch.type)"
              type="text"
              size="small"
              @click="removeBranch(index)"
            >
              <MoreOutlined />
            </a-button>
          </div>
        </div>

        <!-- 条件配置（非 ELSE 分支） -->
        <div v-if="branch.type !== 'ELSE'" class="branch-conditions">
          <div
            v-for="(condition, condIndex) in branch.conditions"
            :key="condIndex"
            class="condition-wrapper"
          >
            <!-- 条件间的逻辑关系标签 -->
            <div v-if="condIndex > 0" class="logic-operator">
              <span class="logic-label">{{
                branch.operator === 'OR' ? '或' : '且'
              }}</span>
            </div>

            <div class="condition-item">
              <!-- 左值：变量选择 -->
              <div class="condition-left">
                <VariableInput
                  v-model="condition.variable"
                  :current-node-id="nodeId"
                  placeholder="选择变量"
                  class="var-select"
                  @change="handleChange"
                />
              </div>

              <!-- 操作符 -->
              <div class="condition-op">
                <a-select
                  v-model:value="condition.operator"
                  size="small"
                  class="op-select"
                  @change="handleChange"
                >
                  <a-select-option value="EQUALS">=</a-select-option>
                  <a-select-option value="NOT_EQUALS">≠</a-select-option>
                  <a-select-option value="GREATER_THAN">&gt;</a-select-option>
                  <a-select-option value="LESS_THAN">&lt;</a-select-option>
                  <a-select-option value="GREATER_OR_EQUAL">≥</a-select-option>
                  <a-select-option value="LESS_OR_EQUAL">≤</a-select-option>
                  <a-select-option value="CONTAINS">包含</a-select-option>
                  <a-select-option value="NOT_CONTAINS">不包含</a-select-option>
                  <a-select-option value="STARTS_WITH">开头是</a-select-option>
                  <a-select-option value="ENDS_WITH">结尾是</a-select-option>
                  <a-select-option value="IS_EMPTY">为空</a-select-option>
                  <a-select-option value="IS_NOT_EMPTY">不为空</a-select-option>
                </a-select>
              </div>

              <!-- 右值：字面量或变量引用 -->
              <div
                v-if="!isNullCheckOperator(condition.operator)"
                class="condition-right"
              >
                <!-- 右值类型切换 -->
                <a-dropdown :trigger="['click']">
                  <a-button size="small" class="value-type-btn">
                    {{ condition.valueIsVariable ? '变量' : '值' }}
                    <DownOutlined />
                  </a-button>
                  <template #overlay>
                    <a-menu
                      @click="
                        (e: { key: string }) =>
                          switchValueType(condition, e.key)
                      "
                    >
                      <a-menu-item key="literal">输入值</a-menu-item>
                      <a-menu-item key="variable">引用参数值</a-menu-item>
                    </a-menu>
                  </template>
                </a-dropdown>

                <!-- 右值输入 -->
                <VariableInput
                  v-if="condition.valueIsVariable"
                  v-model="condition.value"
                  :current-node-id="nodeId"
                  placeholder="选择变量"
                  class="value-input"
                  @change="handleChange"
                />
                <a-input
                  v-else
                  v-model:value="condition.value"
                  placeholder="输入值或引用参数值"
                  size="small"
                  class="value-input"
                  @change="handleChange"
                />
              </div>

              <!-- 删除条件 -->
              <a-button
                v-if="branch.conditions && branch.conditions.length > 1"
                type="text"
                size="small"
                class="remove-condition-btn"
                @click="removeCondition(branch, condIndex)"
              >
                <CloseOutlined />
              </a-button>
            </div>
          </div>

          <!-- 添加条件 -->
          <a-button
            type="link"
            size="small"
            class="add-condition-btn"
            @click="addCondition(branch)"
          >
            <PlusOutlined /> 新增
          </a-button>
        </div>

        <!-- ELSE 分支说明 -->
        <div v-else class="else-content">
          <VariableInput
            v-model="elsePlaceholder"
            :current-node-id="nodeId"
            placeholder="输入值或引用参数值"
            class="else-input"
            disabled
          />
          <div class="else-hint">输出类型: 返回变量</div>
        </div>
      </div>
    </div>

    <!-- 添加分支 -->
    <div class="add-branch-section">
      <a-button
        v-if="canAddElif"
        type="link"
        size="small"
        @click="addElifBranch"
      >
        <PlusOutlined /> 否则如果
      </a-button>
      <a-button
        v-if="!hasElseBranch"
        type="link"
        size="small"
        @click="addElseBranch"
      >
        <PlusOutlined /> 否则
      </a-button>
    </div>
  </div>
</template>

<style scoped lang="less">
// Coze 风格条件分支表单
.selector-node-form {
  padding: 0;

  .form-header {
    margin-bottom: 16px;

    .header-desc {
      font-size: 12px;
      color: #8c8c8c;
      line-height: 1.5;
    }
  }

  .section-title {
    font-size: 14px;
    font-weight: 500;
    color: #262626;
    margin-bottom: 12px;
  }

  .branches-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .branch-card {
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    overflow: hidden;

    .branch-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      background: #fff;
      border-bottom: 1px solid #f0f0f0;

      .branch-header-right {
        display: flex;
        align-items: center;
        gap: 8px;

        .logic-select-header {
          width: 60px;

          :deep(.ant-select-selector) {
            font-size: 12px;
            font-weight: 500;
            color: #fa8c16;
            background: #fff7e6 !important;
            border-color: #ffd591 !important;
          }

          :deep(.ant-select-arrow) {
            color: #fa8c16;
          }
        }
      }
      .branch-title {
        display: flex;
        align-items: center;
        gap: 8px;

        .branch-icon {
          font-weight: bold;
          color: #8c8c8c;
        }

        .branch-name {
          font-size: 13px;
          font-weight: 500;
          color: #262626;
        }

        .branch-priority {
          font-size: 11px;
          color: #8c8c8c;
          background: #f5f5f5;
          padding: 2px 8px;
          border-radius: 4px;
        }
      }
    }

    .branch-conditions {
      padding: 12px;

      .condition-wrapper {
        margin-bottom: 8px;
      }

      .logic-operator {
        display: flex;
        align-items: center;
        margin: 8px 0;
        padding-left: 8px;

        .logic-label {
          font-size: 12px;
          font-weight: 500;
          color: #fa8c16;
          background: #fff7e6;
          border: 1px solid #ffd591;
          padding: 2px 12px;
          border-radius: 4px;
        }
      }

      .condition-item {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;

        .condition-left {
          .var-select {
            min-width: 140px;
          }
        }

        .condition-op {
          .op-select {
            width: 80px;
          }
        }

        .condition-right {
          display: flex;
          align-items: center;
          gap: 6px;
          flex: 1;

          .value-type-btn {
            font-size: 11px;
            padding: 0 8px;
            border-color: #d9d9d9;
            background: #fff;
          }

          .value-input {
            flex: 1;
            min-width: 120px;
          }
        }

        .remove-condition-btn {
          color: #8c8c8c;

          &:hover {
            color: #ff4d4f;
          }
        }
      }

      .add-condition-btn {
        padding: 0;
        font-size: 12px;
        color: #1890ff;
      }
    }

    .else-content {
      padding: 12px;

      .else-input {
        margin-bottom: 8px;
      }

      .else-hint {
        font-size: 11px;
        color: #8c8c8c;
      }
    }

    &.branch-else {
      .branch-header {
        background: #f6ffed;
      }
    }
  }

  .add-branch-section {
    display: flex;
    gap: 16px;
    margin-top: 12px;

    .ant-btn-link {
      padding: 0;
      font-size: 12px;
    }
  }
}
</style>
