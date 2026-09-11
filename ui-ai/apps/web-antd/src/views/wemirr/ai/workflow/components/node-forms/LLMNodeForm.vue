<script setup lang="ts">
/**
 * LLM 节点配置表单
 * 配置大模型调用参数
 * 支持 Vision（图像理解）、Memory（对话记忆）、结构化输出
 */
import { LLM_TYPE_OPTIONS, MODEL_TYPE_TEXT_GEN } from '#/api/ai-workflow/const';
import type {
  LLMNodeConfig,
  StructuredOutput,
} from '#/api/ai-workflow/types';

import { reactive, ref, watch } from 'vue';

import { QuestionCircleOutlined } from '@ant-design/icons-vue';

import { ModelSelect } from '../model-select';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: LLMNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: LLMNodeConfig): void;
}>();

// 默认结构化输出配置
const defaultStructuredOutput: StructuredOutput = {
  enabled: false,
  jsonSchema: '',
  description: '',
  strictMode: false,
};

// 表单数据
const formData = reactive<
  LLMNodeConfig & { structuredOutput: StructuredOutput }
>({
  modelId: undefined,
  modelType: MODEL_TYPE_TEXT_GEN,
  suffix: undefined,
  modelIsDirect: undefined,
  systemPrompt: '',
  promptTemplate: '',
  temperature: 0.7,
  maxTokens: undefined,
  streaming: true,
  outputVariable: '',
  // 高级编排功能
  visionEnabled: false,
  imageVariables: [],
  memoryEnabled: false,
  memoryWindowSize: 10,
  structuredOutput: { ...defaultStructuredOutput },
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
      systemPrompt: config.systemPrompt || '',
      promptTemplate: config.promptTemplate || '',
      temperature: config.temperature ?? 0.7,
      maxTokens: config.maxTokens,
      streaming: config.streaming ?? true,
      outputVariable: config.outputVariable || '',
      // 高级编排功能
      visionEnabled: config.visionEnabled ?? false,
      imageVariables: config.imageVariables || [],
      memoryEnabled: config.memoryEnabled ?? false,
      memoryWindowSize: config.memoryWindowSize ?? 10,
      structuredOutput: config.structuredOutput
        ? { ...defaultStructuredOutput, ...config.structuredOutput }
        : { ...defaultStructuredOutput },
    });
  },
  { immediate: true, deep: true },
);

// 处理配置变更
function handleChange() {
  const config: LLMNodeConfig = {
    modelId: formData.modelId,
    modelType: formData.modelType,
    suffix: formData.suffix || undefined,
    modelIsDirect: formData.modelIsDirect,
    systemPrompt: formData.systemPrompt,
    promptTemplate: formData.promptTemplate,
    temperature: formData.temperature,
    maxTokens: formData.maxTokens,
    streaming: formData.streaming,
    outputVariable: formData.outputVariable,
    visionEnabled: formData.visionEnabled,
    imageVariables: formData.visionEnabled
      ? formData.imageVariables
      : undefined,
    memoryEnabled: formData.memoryEnabled,
    memoryWindowSize: formData.memoryEnabled
      ? formData.memoryWindowSize
      : undefined,
    structuredOutput: formData.structuredOutput.enabled
      ? { ...formData.structuredOutput }
      : undefined,
  };
  emit('update:config', config);
}

</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      v-model:suffix-value="formData.suffix"
      v-model:model-is-direct="formData.modelIsDirect"
      :type-options="LLM_TYPE_OPTIONS"
      @change="handleChange"
    />

    <a-form-item label="系统提示词">
      <a-textarea
        v-model:value="formData.systemPrompt"
        :rows="3"
        placeholder="设置 AI 的角色和行为"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="用户提示词模板">
      <VariableInput
        v-model="formData.promptTemplate"
        :current-node-id="nodeId"
        :placeholder="'使用 {{变量名}} 引用上游节点输出'"
        :multiline="true"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="温度">
      <a-row :gutter="12">
        <a-col :span="18">
          <a-slider
            v-model:value="formData.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            @change="handleChange"
          />
        </a-col>
        <a-col :span="6">
          <a-input-number
            v-model:value="formData.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            size="small"
            style="width: 100%"
            @change="handleChange"
          />
        </a-col>
      </a-row>
    </a-form-item>

    <a-form-item label="最大 Token">
      <a-input-number
        v-model:value="formData.maxTokens"
        :min="1"
        :max="128000"
        placeholder="留空使用模型默认值"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 高级编排功能区域 -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      增强功能
    </a-divider>

    <a-row :gutter="16">
      <a-col :span="8">
        <a-form-item>
          <template #label>
            <span>
              Vision
              <a-tooltip title="启用后可处理图像输入，需要模型支持视觉能力">
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch
            v-model:checked="formData.visionEnabled"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col :span="8">
        <a-form-item>
          <template #label>
            <span>
              Memory
              <a-tooltip title="启用后保持对话上下文，适用于多轮对话场景">
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch
            v-model:checked="formData.memoryEnabled"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col :span="8">
        <a-form-item label="流式输出">
          <a-switch
            v-model:checked="formData.streaming"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
    </a-row>

    <!-- Vision 配置 -->
    <template v-if="formData.visionEnabled">
      <a-form-item label="图像变量">
        <a-select
          v-model:value="formData.imageVariables"
          mode="tags"
          :placeholder="'输入图像变量引用，如 {{start.image}}'"
          @change="handleChange"
        />
        <div class="form-hint">输入包含图像的变量引用，支持多个图像</div>
      </a-form-item>
    </template>

    <!-- Memory 配置 -->
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

    <!-- 结构化输出配置 -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      结构化输出
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          启用结构化输出
          <a-tooltip title="启用后 LLM 将按照 JSON Schema 格式输出结构化数据">
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-switch
        v-model:checked="formData.structuredOutput.enabled"
        @change="handleChange"
      />
    </a-form-item>

    <template v-if="formData.structuredOutput.enabled">
      <a-form-item label="输出描述">
        <a-input
          v-model:value="formData.structuredOutput.description"
          placeholder="描述期望的输出格式"
          @change="handleChange"
        />
      </a-form-item>

      <a-form-item label="JSON Schema">
        <a-textarea
          v-model:value="formData.structuredOutput.jsonSchema"
          :rows="6"
          placeholder='{"type": "object", "properties": {...}}'
          @change="handleChange"
        />
        <div class="form-hint">
          定义输出的 JSON Schema，LLM 将严格按照此格式输出
        </div>
      </a-form-item>

      <a-form-item>
        <template #label>
          <span>
            严格模式
            <a-tooltip
              title="启用后将强制 LLM 严格遵循 Schema，可能影响输出质量"
            >
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
            </a-tooltip>
          </span>
        </template>
        <a-switch
          v-model:checked="formData.structuredOutput.strictMode"
          @change="handleChange"
        />
      </a-form-item>
    </template>

    <a-divider style="margin: 16px 0 12px" />

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: llm_output"
        @change="handleChange"
      />
    </a-form-item>
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

  .variable-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;

    .ant-tag {
      margin-right: 4px;
      font-family: monospace;
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
}
</style>
