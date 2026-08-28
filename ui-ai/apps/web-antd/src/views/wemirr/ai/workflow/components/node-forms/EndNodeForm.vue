<script setup lang="ts">
/**
 * END 节点配置表单
 * 简化设计：只通过 outputs 列表定义输出变量
 * 每个输出变量可以是变量引用或固定值
 */
import { reactive, watch } from 'vue';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

/**
 * 输出字段类型（简化版）
 */
interface OutputField {
  name: string;
  type: 'array' | 'boolean' | 'number' | 'object' | 'string';
  value: string;
  description?: string;
}

/**
 * 结束节点配置（简化版）
 */
interface EndNodeConfig {
  outputs: OutputField[];
}

// Props
interface Props {
  config: EndNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: EndNodeConfig): void;
}>();

// 表单数据
const formData = reactive<EndNodeConfig>({
  outputs: [],
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    formData.outputs = config.outputs
      ? config.outputs.map((o: any) => ({
          name: o.name || '',
          type: o.type || 'string',
          value: o.value || '',
          description: o.description || '',
        }))
      : [];
  },
  { immediate: true, deep: true },
);

/**
 * 添加输出字段
 */
function addOutput() {
  if (!formData.outputs) {
    formData.outputs = [];
  }
  formData.outputs.push({
    name: '',
    type: 'string',
    value: '',
    description: '',
  });
  handleChange();
}

/**
 * 删除输出字段
 */
function removeOutput(index: number) {
  formData.outputs?.splice(index, 1);
  handleChange();
}

/**
 * 处理配置变更
 */
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 输出变量列表 -->
    <div class="outputs-section">
      <div class="section-header">
        <span class="section-title">输出变量</span>
        <a-button type="primary" size="small" @click="addOutput">
          <template #icon><PlusOutlined /></template>
          添加
        </a-button>
      </div>

      <div
        v-if="formData.outputs && formData.outputs.length > 0"
        class="outputs-list"
      >
        <div
          v-for="(output, index) in formData.outputs"
          :key="index"
          class="output-item"
        >
          <div class="output-row">
            <!-- 变量名 -->
            <a-input
              v-model:value="output.name"
              placeholder="变量名"
              class="output-name-input"
              @change="handleChange"
            />
            <!-- 类型 -->
            <a-select
              v-model:value="output.type"
              class="output-type-select"
              @change="handleChange"
            >
              <a-select-option value="string">str.</a-select-option>
              <a-select-option value="number">num.</a-select-option>
              <a-select-option value="boolean">bool.</a-select-option>
              <a-select-option value="array">arr.</a-select-option>
              <a-select-option value="object">obj.</a-select-option>
            </a-select>
            <!-- 变量值（支持引用或固定值） -->
            <VariableInput
              v-model="output.value"
              :current-node-id="nodeId"
              placeholder="输入或引用参数值"
              class="output-value-input"
              @change="handleChange"
            />
            <!-- 删除按钮 -->
            <a-button
              type="text"
              size="small"
              danger
              @click="removeOutput(index)"
            >
              <DeleteOutlined />
            </a-button>
          </div>
        </div>
      </div>

      <div v-else class="empty-hint">
        <span class="hint-text">点击添加按钮定义输出变量</span>
      </div>
    </div>

    <!-- 使用说明 -->
    <a-alert type="info" show-icon class="end-help">
      <template #message>END 节点说明</template>
      <template #description>
        <ul class="help-list">
          <li><strong>变量名</strong>：工作流输出的变量名称</li>
          <li><strong>变量值</strong>：可以是固定值或引用其他节点的变量</li>
          <li>
            变量引用格式: <code v-pre>{{ nodeId.variableName }}</code>
          </li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }
}

.outputs-section {
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

  .outputs-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .output-item {
    .output-row {
      display: flex;
      gap: 8px;
      align-items: center;

      .output-name-input {
        width: 100px;
        flex-shrink: 0;
      }

      .output-type-select {
        width: 70px;
        flex-shrink: 0;
      }

      .output-value-input {
        flex: 1;
        min-width: 0;
      }
    }
  }

  .empty-hint {
    padding: 16px;
    text-align: center;
    background-color: #fafafa;
    border: 1px dashed #d9d9d9;
    border-radius: 6px;

    .hint-text {
      font-size: 12px;
      color: #8c8c8c;
    }
  }
}

.end-help {
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

      code {
        padding: 2px 6px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 3px;
      }
    }
  }
}
</style>
