<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      v-model:suffix-value="formData.suffix"
      v-model:model-is-direct="formData.modelIsDirect"
      :type-options="LLM_TYPE_OPTIONS"
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
    </a-form-item>

    <a-form-item label="推理模式">
      <a-radio-group
        v-model:value="formData.inferenceMode"
        @change="handleChange"
      >
        <a-radio-button value="FUNCTION_CALL">
          <ApiOutlined /> Function Call
        </a-radio-button>
        <a-radio-button value="PROMPT_BASED">
          <FileTextOutlined /> Prompt 模式
        </a-radio-button>
      </a-radio-group>
      <div class="form-hint">
        {{
          formData.inferenceMode === 'FUNCTION_CALL'
            ? 'Function Call 模式使用模型的函数调用能力，提取更精确'
            : 'Prompt 模式通过提示词引导模型提取，适用范围更广'
        }}
      </div>
    </a-form-item>

    <!-- 参数结构定义 -->
    <a-divider orientation="left" style=" margin: 16px 0 12px;font-size: 12px">
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

    <!-- Memory 配置 -->
    <a-divider orientation="left" style=" margin: 16px 0 12px;font-size: 12px">
      高级设置
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          启用记忆
          <a-tooltip title="启用后保持对话历史，适用于多轮提取场景">
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-switch
        v-model:checked="formData.memoryEnabled"
        @change="handleChange"
      />
    </a-form-item>

    <template v-if="formData.memoryEnabled">
      <a-form-item label="记忆窗口大小">
        <a-input-number
          v-model:value="formData.memoryWindowSize"
          :min="1"
          :max="50"
          placeholder="默认: 10"
          style="width: 100%"
          @change="handleChange"
        />
        <div class="form-hint">保留最近的对话轮数</div>
      </a-form-item>
    </template>

    <a-alert type="info" show-icon style="margin-top: 16px">
      <template #message>
        <span style="font-size: 12px">
          提取结果将包含内置状态变量：<code>__is_success</code>（是否成功）和
          <code>__reason</code>（失败原因）
        </span>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * 参数提取器节点配置表单
 * 从自然语言文本中提取结构化参数
 */
import { LLM_TYPE_OPTIONS, MODEL_TYPE_TEXT_GEN } from '#/api/ai-workflow/const';
import type {
  ExtractParameter,
  InferenceMode,
  ParameterExtractorConfig,
  ParameterType,
} from '#/api/ai-workflow/types';

import {
  ApiOutlined,
  DeleteOutlined,
  FileTextOutlined,
  HolderOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';
import { reactive, ref, watch } from 'vue';
import draggable from 'vuedraggable';

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
  modelType: MODEL_TYPE_TEXT_GEN,
  suffix: undefined,
  modelIsDirect: undefined,
  inputVariable: '',
  instructions: '',
  parameters: [...defaultParameters],
  inferenceMode: 'FUNCTION_CALL' as InferenceMode,
  memoryEnabled: false,
  memoryWindowSize: 10,
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MODEL_TYPE_TEXT_GEN,
      suffix: config.suffix,
      modelIsDirect: config.modelIsDirect,
      inputVariable: config.inputVariable || '',
      instructions: config.instructions || '',
      parameters:
        config.parameters && config.parameters.length > 0
          ? config.parameters.map((p) => ({ ...p }))
          : [...defaultParameters],
      inferenceMode: config.inferenceMode || 'FUNCTION_CALL',
      memoryEnabled: config.memoryEnabled ?? false,
      memoryWindowSize: config.memoryWindowSize ?? 10,
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
    modelType: formData.modelType,
    suffix: formData.suffix || undefined,
    modelIsDirect: formData.modelIsDirect,
    inputVariable: formData.inputVariable,
    instructions: formData.instructions,
    parameters: formData.parameters?.filter(
      (p: ExtractParameter) => p.name && p.type,
    ),
    inferenceMode: formData.inferenceMode,
    memoryEnabled: formData.memoryEnabled,
    memoryWindowSize: formData.memoryEnabled
      ? formData.memoryWindowSize
      : undefined,
  };
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

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  :deep(.ant-radio-button-wrapper) {
    font-size: 12px;
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
