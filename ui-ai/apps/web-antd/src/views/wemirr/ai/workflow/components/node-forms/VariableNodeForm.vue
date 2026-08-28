<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <div class="assignments">
      <div
        v-for="(assignment, index) in formData.assignments"
        :key="index"
        class="assignment-item"
      >
        <div class="assignment-header">
          <span>赋值 {{ index + 1 }}</span>
          <a-button
            v-if="formData.assignments.length > 1"
            type="text"
            size="small"
            danger
            @click="removeAssignment(index)"
          >
            删除
          </a-button>
        </div>

        <a-form-item label="目标变量名" required>
          <a-input
            v-model:value="assignment.variableName"
            placeholder="例如: answer"
            @change="handleChange"
          />
        </a-form-item>

        <a-form-item label="赋值类型">
          <a-select
            v-model:value="assignment.type"
            @change="handleAssignmentTypeChange(assignment)"
          >
            <a-select-option value="LITERAL">字面量</a-select-option>
            <a-select-option value="VARIABLE">变量引用</a-select-option>
            <a-select-option value="EXPRESSION">表达式</a-select-option>
          </a-select>
        </a-form-item>

        <a-form-item label="变量值">
          <VariableInput
            v-model="assignment.value"
            :current-node-id="nodeId"
            :placeholder="getValuePlaceholder(assignment.type)"
            :multiline="assignment.type !== 'VARIABLE'"
            :max-rows="4"
            @change="handleChange"
          />
        </a-form-item>

        <a-form-item v-if="assignment.type === 'EXPRESSION'" label="转换表达式">
          <VariableInput
            v-model="assignment.transformExpression"
            :current-node-id="nodeId"
            placeholder="例如: {{input.text}}.trim()"
            @change="handleChange"
          />
        </a-form-item>
      </div>
    </div>

    <a-button type="dashed" block @click="addAssignment">添加赋值</a-button>
  </a-form>
</template>

<script setup lang="ts">
/**
 * 变量赋值节点配置表单
 * 输出 assignments 结构。
 */
import type {
  Assignment,
  AssignmentType,
  VariableAssignerConfig,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import { VariableInput } from '../variable-selector';

interface Props {
  config: VariableAssignerConfig;
  nodeId: string;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:config', config: VariableAssignerConfig): void;
}>();

const formData = reactive<Required<VariableAssignerConfig>>({
  assignments: [createAssignment()],
});

watch(
  () => props.config,
  (config) => {
    formData.assignments = config.assignments?.length
      ? config.assignments.map((assignment) => ({ ...assignment }))
      : [createAssignment()];
  },
  { immediate: true, deep: true },
);

function createAssignment(): Assignment {
  return {
    variableName: '',
    type: 'LITERAL',
    value: '',
    variableType: 'string',
    overwrite: true,
  };
}

function getValuePlaceholder(type: AssignmentType): string {
  if (type === 'VARIABLE') {
    return '例如: {{start.query}}';
  }
  if (type === 'EXPRESSION') {
    return '表达式输入值，例如: {{input.text}}';
  }
  return '静态值，例如: hello 或 { "ok": true }';
}

function addAssignment() {
  formData.assignments.push(createAssignment());
  handleChange();
}

function removeAssignment(index: number) {
  formData.assignments.splice(index, 1);
  if (formData.assignments.length === 0) {
    formData.assignments.push(createAssignment());
  }
  handleChange();
}

function handleAssignmentTypeChange(assignment: Assignment) {
  if (assignment.type !== 'EXPRESSION') {
    assignment.transformExpression = undefined;
  }
  handleChange();
}

function handleChange() {
  emit('update:config', {
    assignments: formData.assignments.map((assignment) => ({ ...assignment })),
  });
}
</script>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 12px;
  }
}

.assignments {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.assignment-item {
  padding: 12px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  background: #fafafa;
}

.assignment-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #595959;
}
</style>
