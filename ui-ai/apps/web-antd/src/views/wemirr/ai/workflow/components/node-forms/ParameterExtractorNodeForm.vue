<script setup lang="ts">
import type {
  ExtractParameter,
  ParameterExtractorConfig,
  ParameterType,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import {
  DeleteOutlined,
  HolderOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import draggable from 'vuedraggable';

/**
 * 参数提取器节点配置表单
 * 使用 LLM（文生文）从自然语言文本中提取结构化参数
 * 【设计对齐 2026-09】模型固定为 text_to_text，「直连/后缀」概念已废弃
 */
import { CHAT_TYPE_OPTIONS, MT_TEXT_TO_TEXT } from '#/api/ai-workflow/const';

import { ModelSelect } from '../model-select';
import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: ParameterExtractorConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ParameterExtractorConfig): void;
}>();

// 默认参数
const defaultParameters: ExtractParameter[] = [
  {
    name: 'param1',
    type: 'string' as ParameterType,
    description: '',
    required: false,
  },
];

// 表单数据
const formData = reactive<ParameterExtractorConfig>({
  modelId: undefined,
  modelType: MT_TEXT_TO_TEXT,
  inputVariable: '',
  instructions: '',
  parameters: [...defaultParameters],
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MT_TEXT_TO_TEXT,
      inputVariable: config.inputVariable || '',
      instructions: config.instructions || '',
      parameters:
        config.parameters && config.parameters.length > 0
          ? config.parameters.map((p) => ({ ...p }))
          : [...defaultParameters],
    });
  },
  { immediate: true, deep: true },
);

// 添加参数
function addParameter() {
  formData.parameters = formData.parameters || [];
  const newIndex = formData.parameters.length + 1;
  formData.parameters.push({
    name: `param${newIndex}`,
    type: 'string' as ParameterType,
    description: '',
    required: false,
  });
  handleChange();
}

// 移除参数
function removeParameter(index: number) {
  formData.parameters?.splice(index, 1);
  handleChange();
}

// 处理配置变更
function handleChange() {
  const config: ParameterExtractorConfig = {
    modelId: formData.modelId,
    modelType: MT_TEXT_TO_TEXT,
    inputVariable: formData.inputVariable,
    instructions: formData.instructions,
    parameters: formData.parameters?.filter(
      (p: ExtractParameter) => p.name && p.type,
    ),
  };
  emit('update:config', config);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      :type-options="CHAT_TYPE_OPTIONS"
      placeholder="选择用于参数提取的模型"
      @change="handleChange"
    />

    <a-form-item label="输入变量" required>
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        placeholder="{{start.text}} 或 {{nodeName.output}}"
        @change="handleChange"
      />
      <div class="form-hint">输入要提取参数的文本变量引用</div>
    </a-form-item>

    <a-form-item label="提取指导说明">
      <a-textarea
        v-model:value="formData.instructions"
        :rows="3"
        placeholder="描述要提取的参数和提取规则，帮助模型更准确地提取"
        @change="handleChange"
      />
      <div class="form-hint">
        固定以 Prompt 方式提取：提示词里带上参数 schema，并约束模型输出 JSON
      </div>
    </a-form-item>

    <!-- 参数结构定义 -->
    <a-divider orientation="left" style="margin: 16px 0 12px; font-size: 12px">
      参数结构定义
    </a-divider>

    <div class="parameters-section">
      <draggable
        v-model="formData.parameters"
        item-key="name"
        handle=".drag-handle"
        @change="handleChange"
      >
        <template #item="{ element: param, index }">
          <div class="parameter-item">
            <div class="parameter-header">
              <HolderOutlined class="drag-handle" />
              <span class="parameter-index">参数 {{ index + 1 }}</span>
              <a-button
                type="text"
                danger
                size="small"
                @click="removeParameter(index)"
              >
                <DeleteOutlined />
              </a-button>
            </div>
            <div class="parameter-content">
              <a-row :gutter="8">
                <a-col :span="10">
                  <a-input
                    v-model:value="param.name"
                    placeholder="参数名"
                    size="small"
                    @change="handleChange"
                  />
                </a-col>
                <a-col :span="8">
                  <a-select
                    v-model:value="param.type"
                    size="small"
                    style="width: 100%"
                    @change="handleChange"
                  >
                    <a-select-option value="string">字符串</a-select-option>
                    <a-select-option value="number">数字</a-select-option>
                    <a-select-option value="boolean">布尔值</a-select-option>
                    <a-select-option value="array">数组</a-select-option>
                    <a-select-option value="object">对象</a-select-option>
                  </a-select>
                </a-col>
                <a-col :span="6">
                  <a-checkbox
                    v-model:checked="param.required"
                    size="small"
                    @change="handleChange"
                  >
                    必填
                  </a-checkbox>
                </a-col>
              </a-row>
              <a-input
                v-model:value="param.description"
                placeholder="参数描述（帮助模型理解要提取什么）"
                size="small"
                style="margin-top: 8px"
                @change="handleChange"
              />
              <a-select
                v-if="param.type === 'string'"
                v-model:value="param.enumValues"
                mode="tags"
                placeholder="枚举值（可选）"
                size="small"
                style="width: 100%; margin-top: 8px"
                @change="handleChange"
              />
            </div>
          </div>
        </template>
      </draggable>

      <a-button type="dashed" size="small" block @click="addParameter">
        <PlusOutlined /> 添加参数
      </a-button>
    </div>

    <div class="form-hint" style="margin-top: 8px">
      每个参数将作为独立的输出变量，可在下游节点中引用
    </div>

    <!-- 说明：平台无会话级存储，不提供“对话记忆”配置（后端也不读该字段） -->
    <a-alert type="info" show-icon style="margin-top: 16px">
      <template #message>
        <span style="font-size: 12px">
          提取结果将包含内置状态变量：<code>__is_success</code>（是否成功）和
          <code>__reason</code>（失败原因）；提取未成功时节点不报错，可在下游用
          <code>__is_success</code> 做条件分支
        </span>
      </template>
    </a-alert>
  </a-form>
</template>

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

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  .parameters-section {
    .parameter-item {
      margin-bottom: 12px;
      padding: 12px;
      background: #fafafa;
      border: 1px solid #f0f0f0;
      border-radius: 6px;

      .parameter-header {
        display: flex;
        align-items: center;
        margin-bottom: 8px;

        .drag-handle {
          cursor: move;
          color: #bfbfbf;
          margin-right: 8px;

          &:hover {
            color: #1890ff;
          }
        }

        .parameter-index {
          flex: 1;
          font-size: 12px;
          font-weight: 500;
          color: #595959;
        }
      }

      .parameter-content {
        :deep(.ant-input) {
          font-size: 12px;
        }

        :deep(.ant-select) {
          font-size: 12px;
        }

        :deep(.ant-checkbox-wrapper) {
          font-size: 12px;
        }
      }
    }
  }

  :deep(.ant-alert-message) {
    code {
      background: #f5f5f5;
      padding: 2px 4px;
      border-radius: 3px;
      font-family: monospace;
    }
  }
}
</style>
