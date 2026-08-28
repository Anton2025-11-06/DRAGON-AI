<script setup lang="ts">
import type { ModelConfig } from '../api';

import { GlobalOutlined, ThunderboltOutlined } from '@ant-design/icons-vue';
import { Select } from 'ant-design-vue';
import { Sender } from 'ant-design-x-vue';

// ==================== Props ====================
withDefaults(
  defineProps<{
    /** 加载状态 */
    loading?: boolean;
    /** 模型列表 */
    models?: ModelConfig[];
    /** 选中的模型ID */
    selectedModelId?: number;
    /** 是否显示模型选择器 */
    showModelSelect?: boolean;
  }>(),
  {
    models: () => [],
    showModelSelect: true,
  },
);

// ==================== Emits ====================
const emit = defineEmits<{
  cancel: [];
  submit: [value: string];
}>();
// ==================== Model ====================
const inputValue = defineModel<string>('value', { default: '' });
const modelId = defineModel<number | undefined>('modelId');
const deepThinking = defineModel<boolean>('deepThinking', { default: false });
const webSearch = defineModel<boolean>('webSearch', { default: false });

// ==================== 事件处理 ====================
function handleSubmit(value: string) {
  emit('submit', value);
}

function handleCancel() {
  emit('cancel');
}

function toggleDeepThinking() {
  deepThinking.value = !deepThinking.value;
}

function toggleWebSearch() {
  webSearch.value = !webSearch.value;
}
</script>

<template>
  <div class="chat-input-wrapper">
    <div class="chat-input-container">
      <div class="sender-box">
        <!-- 输入框 -->
        <Sender
          v-model:value="inputValue"
          :loading="loading"
          placeholder="给 AI 发送消息"
          @submit="handleSubmit"
          @cancel="handleCancel"
        />

        <!-- 底部选项栏 -->
        <div class="input-footer">
          <div class="input-options">
            <button
              class="option-btn"
              :class="{ active: deepThinking }"
              @click="toggleDeepThinking"
            >
              <ThunderboltOutlined />
              <span>深度思考</span>
            </button>
            <button
              class="option-btn"
              :class="{ active: webSearch }"
              @click="toggleWebSearch"
            >
              <GlobalOutlined />
              <span>联网搜索</span>
            </button>
          </div>

          <!-- 模型选择 -->
          <Select
            v-if="showModelSelect && models.length > 0"
            v-model:value="modelId"
            :options="models.map((m) => ({ label: m.name, value: m.id }))"
            placeholder="选择模型"
            :bordered="false"
            size="small"
            class="model-select"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.chat-input-wrapper {
  flex-shrink: 0;
  padding: 8px 0 12px;
  background: var(--component-background, #fff);
}

.chat-input-container {
  padding: 0 24px;
  margin: 0 auto;
}

.sender-box {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 3%);

  :deep(.ant-sender) {
    border: none !important;
    box-shadow: none !important;

    .ant-sender-input {
      padding: 10px 14px;
      font-size: 14px;

      textarea {
        &::placeholder {
          color: #bbb;
        }
      }
    }

    .ant-sender-actions {
      padding: 4px 10px;
    }
  }
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-top: 1px solid #f5f5f5;
}

.input-options {
  display: flex;
  gap: 8px;
}

.option-btn {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 6px 12px;
  font-size: 13px;
  color: #666;
  cursor: pointer;
  background: #fff;
  border: 1px solid #e5e5e5;
  border-radius: 20px;
  transition: all 0.2s;

  .anticon {
    font-size: 14px;
  }

  &:hover {
    color: #1890ff;
    border-color: #1890ff;
  }

  &.active {
    color: #1890ff;
    background: #e6f7ff;
    border-color: #1890ff;
  }
}

.model-select {
  :deep(.ant-select-selector) {
    font-size: 13px;
    color: #666;
  }
}
</style>
