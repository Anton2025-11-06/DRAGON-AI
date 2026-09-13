<script setup lang="ts">
/**
 * LLM 节点配置表单（对齐 common_model 12 能力类型）
 * 依据所选模型登记的能力类型（category）动态展示对应输入项：
 * - 文生文：系统/用户提示词、温度、MaxToken、深度思考、流式、Vision、Memory、结构化输出
 * - 图片理解/视频理解/OCR：媒体输入变量 +（提示词）+ 采样参数
 * - 文本向量：文本输入；图片向量：图片输入
 * - 文本重排：查询变量 + 文档变量 + topN
 * - 文生图/文生视频/图生视频/文生音频/音频转文字：对应生成入参
 * 上游媒体/文本均以 {{节点.变量}} 引用，或直接粘贴 URL/文本（后端 _build_kwargs 解析）。
 */
import { computed, reactive, ref, watch } from 'vue';

import {
  MODEL_TYPES_STREAMABLE,
  MT_AUDIO_TO_TEXT,
  MT_IMAGE_EMBEDDING,
  MT_IMAGE_TO_VIDEO,
  MT_IMAGE_UNDERSTAND,
  MT_OCR,
  MT_TEXT_EMBEDDING,
  MT_TEXT_RERANK,
  MT_TEXT_TO_AUDIO,
  MT_TEXT_TO_IMAGE,
  MT_TEXT_TO_TEXT,
  MT_TEXT_TO_VIDEO,
  MT_VIDEO_UNDERSTAND,
  LLM_TYPE_OPTIONS,
} from '#/api/ai-workflow/const';
import type { LLMNodeConfig, StructuredOutput } from '#/api/ai-workflow/types';

import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { ModelSelect } from '../model-select';
import { VariableInput } from '../variable-selector';

import * as modelApi from '../../../model-plaza/api';
import ModelTryPanel from '../../../model-plaza/components/ModelTryPanel.vue';

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

const defaultStructuredOutput: StructuredOutput = {
  enabled: false,
  jsonSchema: '',
  description: '',
  strictMode: false,
};

// 表单数据（含 12 能力类型全部入参字段）
const formData = reactive<
  LLMNodeConfig & { structuredOutput: StructuredOutput }
>({
  modelId: undefined,
  modelType: MT_TEXT_TO_TEXT,
  systemPrompt: '',
  promptTemplate: '',
  temperature: 0.7,
  maxTokens: undefined,
  thinking: false,
  streaming: true,
  outputVariable: '',
  inputVariable: '',
  imageVariable: '',
  audioVariable: '',
  videoVariable: '',
  queryVariable: '',
  documentsVariable: '',
  topN: undefined,
  size: '',
  imageN: undefined,
  voice: '',
  visionEnabled: false,
  imageVariables: [],
  memoryEnabled: false,
  memoryWindowSize: 10,
  structuredOutput: { ...defaultStructuredOutput },
});

watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MT_TEXT_TO_TEXT,
      systemPrompt: config.systemPrompt || '',
      promptTemplate: config.promptTemplate || '',
      temperature: config.temperature ?? 0.7,
      maxTokens: config.maxTokens,
      thinking: config.thinking ?? false,
      streaming: config.streaming ?? true,
      outputVariable: config.outputVariable || '',
      inputVariable: config.inputVariable || '',
      imageVariable: config.imageVariable || '',
      audioVariable: config.audioVariable || '',
      videoVariable: config.videoVariable || '',
      queryVariable: config.queryVariable || '',
      documentsVariable: config.documentsVariable || '',
      topN: config.topN,
      size: config.size || '',
      imageN: config.imageN,
      voice: config.voice || '',
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

// ==================== 能力类型派生显示开关 ====================

const cat = computed(() => formData.modelType || MT_TEXT_TO_TEXT);
const isChat = computed(() => cat.value === MT_TEXT_TO_TEXT);
// 提示词输入：文生文/图片理解/视频理解/OCR/文生图/文生视频/图生视频/文生音频
const showPrompt = computed(() =>
  [
    MT_TEXT_TO_TEXT,
    MT_IMAGE_UNDERSTAND,
    MT_VIDEO_UNDERSTAND,
    MT_OCR,
    MT_TEXT_TO_IMAGE,
    MT_TEXT_TO_VIDEO,
    MT_IMAGE_TO_VIDEO,
    MT_TEXT_TO_AUDIO,
  ].includes(cat.value),
);
// 采样参数（温度/MaxToken）：仅对话族与理解族有意义
const showSampling = computed(() =>
  [MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR].includes(
    cat.value,
  ),
);
const showStream = computed(() => MODEL_TYPES_STREAMABLE.includes(cat.value));
const showThinking = computed(() => showStream.value);
const showVision = computed(() => isChat.value);
const showMemory = computed(() => isChat.value);
const showStructured = computed(() => isChat.value);
const showInput = computed(() => cat.value === MT_TEXT_EMBEDDING);
const showImageVar = computed(() =>
  [MT_IMAGE_UNDERSTAND, MT_OCR, MT_IMAGE_EMBEDDING, MT_IMAGE_TO_VIDEO].includes(
    cat.value,
  ),
);
const showVideoVar = computed(() => cat.value === MT_VIDEO_UNDERSTAND);
const showAudioVar = computed(() => cat.value === MT_AUDIO_TO_TEXT);
const showRerank = computed(() => cat.value === MT_TEXT_RERANK);
const showSize = computed(() =>
  [MT_TEXT_TO_IMAGE, MT_TEXT_TO_VIDEO, MT_IMAGE_TO_VIDEO].includes(cat.value),
);
const showImageN = computed(() => cat.value === MT_TEXT_TO_IMAGE);
const showVoice = computed(() => cat.value === MT_TEXT_TO_AUDIO);

// ==================== 模型测试台（复用 ModelTryPanel，不影响配置字段保存）====================
const testOpen = ref(false);
const testLoading = ref(false);
const testRunning = ref(false);
const testDetail = ref<null | modelApi.ModelDetailRep>(null);
const testResult = ref<modelApi.ModelTestRep | null>(null);
const testError = ref<null | string>(null);
const getTestContainer = () =>
  typeof document === 'undefined' ? undefined : document.body;

/** 拉取所选模型详情（supports 能力位 / common_params 常用参数 / 凭据），打开测试台 */
async function openModelTest() {
  const id = Number(formData.modelId);
  if (!id) {
    message.warning('请先选择模型');
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  testError.value = null;
  try {
    testDetail.value = await modelApi.GetDetail(id);
    testOpen.value = true;
  } catch (e: any) {
    message.error(e?.message || '模型详情加载失败');
  } finally {
    testLoading.value = false;
  }
}

/** 测试台提交：携带 inputs/stream/thinking/params 调 /models/test */
async function onTryRun(payload: {
  inputs: Record<string, any>;
  params: Record<string, any>;
  stream: boolean;
  thinking: boolean;
}) {
  const d = testDetail.value;
  if (!d) return;
  testRunning.value = true;
  testError.value = null;
  try {
    const res = await modelApi.TestModel({
      category: d.category,
      provider: d.provider,
      model_name: d.model_name,
      base_url: d.base_url,
      api_key: d.api_key,
      inputs: payload.inputs,
      stream: payload.stream,
      thinking: payload.thinking,
      params: payload.params,
    });
    testResult.value = res || null;
  } catch (e: any) {
    testResult.value = null;
    testError.value = e?.message || '测试请求失败，请稍后重试';
  } finally {
    testRunning.value = false;
  }
}

function handleChange() {
  const config: LLMNodeConfig = {
    modelId: formData.modelId,
    modelType: formData.modelType,
    outputVariable: formData.outputVariable,
    promptTemplate: formData.promptTemplate,
    systemPrompt: formData.systemPrompt,
    temperature: formData.temperature,
    maxTokens: formData.maxTokens,
    thinking: formData.thinking,
    streaming: formData.streaming,
    inputVariable: formData.inputVariable,
    imageVariable: formData.imageVariable,
    audioVariable: formData.audioVariable,
    videoVariable: formData.videoVariable,
    queryVariable: formData.queryVariable,
    documentsVariable: formData.documentsVariable,
    topN: formData.topN,
    size: formData.size || undefined,
    imageN: formData.imageN,
    voice: formData.voice || undefined,
    visionEnabled: formData.visionEnabled,
    imageVariables: formData.visionEnabled ? formData.imageVariables : undefined,
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

// 类型切换：清空与该类型无关的输入，避免脏数据传给后端
function handleTypeChange() {
  handleChange();
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      :type-options="LLM_TYPE_OPTIONS"
      @change="handleTypeChange"
    />

    <div class="test-bar">
      <a-button size="small" :loading="testLoading" @click="openModelTest">
        测试该模型
      </a-button>
      <span class="form-hint">按当前所选模型试跑（不影响配置保存）</span>
    </div>

    <!-- 文本输入（text_embedding） -->
    <a-form-item v-if="showInput" label="文本输入变量">
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        :placeholder="'待向量化的文本，如 {{start.text}}'"
        @change="handleChange"
      />
      <div class="form-hint">可引用上游变量，或直接填写文本</div>
    </a-form-item>

    <!-- 重排：查询 + 文档 -->
    <template v-if="showRerank">
      <a-form-item label="查询变量（Query）">
        <VariableInput
          v-model="formData.queryVariable"
          :current-node-id="nodeId"
          :placeholder="'重排查询语句，如 {{start.query}}'"
          @change="handleChange"
        />
      </a-form-item>
      <a-form-item label="文档变量（Documents）">
        <VariableInput
          v-model="formData.documentsVariable"
          :current-node-id="nodeId"
          :placeholder="'待重排文档数组变量，如 {{retrieval.documents}}'"
          @change="handleChange"
        />
        <div class="form-hint">引用一个字符串数组变量（如知识检索的文档列表）</div>
      </a-form-item>
      <a-form-item label="保留数量 Top N">
        <a-input-number
          v-model:value="formData.topN"
          :min="1"
          placeholder="默认返回全部"
          style="width: 100%"
          @change="handleChange"
        />
      </a-form-item>
    </template>

    <!-- 图片输入变量 -->
    <a-form-item v-if="showImageVar" label="图片输入变量">
      <VariableInput
        v-model="formData.imageVariable"
        :current-node-id="nodeId"
        :placeholder="'图片 URL 或引用，如 {{start.image}}'"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 视频输入变量 -->
    <a-form-item v-if="showVideoVar" label="视频输入变量">
      <VariableInput
        v-model="formData.videoVariable"
        :current-node-id="nodeId"
        :placeholder="'视频 URL 或引用，如 {{start.video}}'"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 音频输入变量 -->
    <a-form-item v-if="showAudioVar" label="音频输入变量">
      <VariableInput
        v-model="formData.audioVariable"
        :current-node-id="nodeId"
        :placeholder="'音频 URL 或引用，如 {{start.audio}}'"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 提示词（含文生文/理解/生成类） -->
    <a-form-item v-if="showPrompt" :label="isChat ? '用户提示词模板' : '提示词'">
      <VariableInput
        v-model="formData.promptTemplate"
        :current-node-id="nodeId"
        :placeholder="'使用 {{变量名}} 引用上游节点输出'"
        :multiline="true"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 生成参数 -->
    <a-form-item v-if="showSize" label="生成尺寸 size">
      <a-input
        v-model:value="formData.size"
        placeholder="如 1024x1024 / 1280*720，留空用模型默认"
        @change="handleChange"
      />
    </a-form-item>
    <a-form-item v-if="showImageN" label="生成图片数量">
      <a-input-number
        v-model:value="formData.imageN"
        :min="1"
        :max="4"
        placeholder="默认 1"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>
    <a-form-item v-if="showVoice" label="音色 voice">
      <a-input
        v-model:value="formData.voice"
        placeholder="如 Cherry / 中文女声，留空用模型默认"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 文生文：系统提示词 -->
    <a-form-item v-if="isChat" label="系统提示词">
      <a-textarea
        v-model:value="formData.systemPrompt"
        :rows="3"
        placeholder="设置 AI 的角色和行为"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 采样参数 -->
    <a-form-item v-if="showSampling" label="温度">
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

    <a-form-item v-if="showSampling" label="最大 Token">
      <a-input-number
        v-model:value="formData.maxTokens"
        :min="1"
        :max="128000"
        placeholder="留空使用模型默认值"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 增强功能（流式 / 思考 / Vision / Memory） -->
    <a-divider
      v-if="showStream || showVision || showMemory"
      orientation="left"
      style="font-size: 12px; margin: 16px 0 12px"
    >
      增强功能
    </a-divider>
    <a-row :gutter="16">
      <a-col v-if="showStream" :span="8">
        <a-form-item label="流式输出">
          <a-switch
            v-model:checked="formData.streaming"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col v-if="showThinking" :span="8">
        <a-form-item>
          <template #label>
            <span>
              深度思考
              <a-tooltip title="启用后模型返回推理过程（reasoning），仅部分模型支持">
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch v-model:checked="formData.thinking" @change="handleChange" />
        </a-form-item>
      </a-col>
      <a-col v-if="showVision" :span="8">
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
      <a-col v-if="showMemory" :span="8">
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
    </a-row>

    <!-- Vision 配置 -->
    <template v-if="showVision && formData.visionEnabled">
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
    <template v-if="showMemory && formData.memoryEnabled">
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

    <!-- 结构化输出配置（仅文生文） -->
    <template v-if="showStructured">
      <a-divider
        orientation="left"
        style="font-size: 12px; margin: 16px 0 12px"
      >
        结构化输出
      </a-divider>
      <a-form-item>
        <template #label>
          <span>
            启用结构化输出
            <a-tooltip
              title="启用后 LLM 将按照 JSON Schema 格式输出结构化数据"
            >
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
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
    </template>

    <a-divider style="margin: 16px 0 12px" />

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: llm_output"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 模型测试台：按所选模型 detail 的 supports_*/common_params 驱动，试跑走 /models/test -->
    <a-modal
      :open="testOpen"
      title="模型测试"
      width="680px"
      :footer="null"
      :destroy-on-close="true"
      :get-container="getTestContainer"
      @cancel="testOpen = false"
    >
      <ModelTryPanel
        v-if="testDetail"
        :category="testDetail.category"
        :supports-stream="!!testDetail.supports_stream"
        :supports-thinking="!!testDetail.supports_thinking"
        :common-params="testDetail.common_params || []"
        :running="testRunning"
        :show-result="true"
        :result="testResult"
        :result-error="testError"
        submit-text="运行测试"
        @run="onTryRun"
      />
    </a-modal>
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

  .test-bar {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 16px;
  }

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }
}
</style>
