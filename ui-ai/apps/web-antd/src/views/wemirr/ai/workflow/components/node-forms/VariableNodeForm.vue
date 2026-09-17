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

import { VariableInput, VariableSelector } from '../variable-selector';

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
    return '变量引用，例如: {{nodes.xx.output}}';
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

/**
 * 目标变量名引用上游变量（BUG7）：名字不再只能是字面量，
 * 选中后把变量引用（如 {{nodes.xx.output}}）写回 variableName，后端运行时解析取值作为变量名。
 */
function handleTargetReferenceSelect(
  assignment: Assignment,
  reference: string,
) {
  assignment.variableName = reference;
  handleChange();
}

function handleChange() {
  emit('update:config', {
    assignments: formData.assignments.map((assignment) => ({ ...assignment })),
  });
}
</script>

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
          <div class="target-name-row">
            <a-input
              v-model:value="assignment.variableName"
              placeholder="自定义变量名，例如: answer"
              @change="handleChange"
            />
            <VariableSelector
              :current-node-id="nodeId"
              button-text="引用上游"
              @select="
                (reference: string) =>
                  handleTargetReferenceSelect(assignment, reference)
              "
            />
          </div>
          <div class="form-hint">
            直接输入即自定义变量名；点「引用上游」选中的变量会在运行时取其值作为目标变量名
          </div>
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
            placeholder="例如: {{nodes.xx.output}}.name 或 {{nodes.xx.output}}[0].name"
            @change="handleChange"
          />
        </a-form-item>
      </div>
    </div>

    <a-button type="dashed" block @click="addAssignment">添加赋值</a-button>
  </a-form>
</template>

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

.target-name-row {
  display: flex;
  gap: 4px;
  align-items: center;
}

.form-hint {
  margin-top: 4px;
  font-size: 11px;
  color: #8c8c8c;
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
