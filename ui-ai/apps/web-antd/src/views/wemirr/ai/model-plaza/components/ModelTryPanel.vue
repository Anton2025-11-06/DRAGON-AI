<script setup lang="ts">
/**
 * 模型测试/体验台（可复用）：供「添加模型弹窗测试」「模型体验页」「工作流 LLM 节点测试」三处复用。
 *
 * 职责：仅负责「输入采集 + 参数装配」，不直接发起调用；提交时 emit('run', payload)，
 * 由使用方按自身上下文执行（体验页走网关流式 invokeModel；测试走 /models/test）。
 *
 * 设计对齐需求 2.5.1.1~2.5.1.4.2：
 *  - 顶部：支持流消息 / 支持思考模式 开关（仅当模型声明 supports_* 时显示）
 *  - 常用参数：每行 参数名 / 默认值(按 type 控件可改) / 参数说明 / 参数类型
 *  - 输入区：按 category 复用 12 类型字段配置；url 字段旁提供「上传」按钮（调 service_file 回填可下载 URL）
 *  - 产出：{ inputs, stream, thinking, params }
 */
import type { CommonParam, CommonParamType } from '../api';

import { computed, reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import {
  DeleteOutlined,
  LoadingOutlined,
  PlusOutlined,
  SendOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';

import { UploadFile } from '../api';

defineOptions({ name: 'ModelTryPanel' });

// ==================== 类型 ====================

export interface TryField {
  key: string;
  label: string;
  type: 'text' | 'textarea' | 'url' | 'voice' | 'documents';
  placeholder: string;
  required?: boolean;
  rows?: number;
  extra?: string;
}

export interface TryFormConfig {
  category: string;
  label: string;
  hint: string;
  fields: TryField[];
}

export interface TryPayload {
  inputs: Record<string, any>;
  stream: boolean;
  thinking: boolean;
  params: Record<string, any>;
}

// ==================== 12 类型输入配置（category → 输入字段） ====================

const TYPE_FORMS: TryFormConfig[] = [
  {
    category: 'text_to_text',
    label: '文生文',
    hint: '输入提示词，模型将回复内容（可开启流式 / 深度思考）。',
    fields: [
      { key: 'prompt', label: '提示词', type: 'textarea', rows: 6, required: true, placeholder: '例如：用 80 字介绍杭州西湖的四季…' },
    ],
  },
  {
    category: 'image_understand',
    label: '图片理解',
    hint: '提供图片地址与提问，模型将基于图片内容作答。',
    fields: [
      { key: 'image_url', label: '图片地址', type: 'url', required: true, placeholder: 'https://…/example.jpg', extra: '可直接上传或填写公网可访问直链' },
      { key: 'prompt', label: '提问', type: 'textarea', rows: 4, required: true, placeholder: '例如：图里有什么？描述一下画面细节' },
    ],
  },
  {
    category: 'video_understand',
    label: '视频理解',
    hint: '提供视频地址与提问，模型将基于视频内容作答。',
    fields: [
      { key: 'video_url', label: '视频地址', type: 'url', required: true, placeholder: 'https://…/example.mp4', extra: '可直接上传或填写公网可访问直链' },
      { key: 'prompt', label: '提问', type: 'textarea', rows: 4, required: true, placeholder: '例如：视频里发生了什么？' },
    ],
  },
  {
    category: 'ocr',
    label: 'OCR 文字识别',
    hint: '提供图片地址，模型将识别并输出图中的全部文字。',
    fields: [
      { key: 'image_url', label: '图片地址', type: 'url', required: true, placeholder: 'https://…/scan.png', extra: '可直接上传或填写公网可访问直链' },
    ],
  },
  {
    category: 'audio_to_text',
    label: '音频转文字',
    hint: '提供音频地址，模型将转写为文字。',
    fields: [
      { key: 'audio_url', label: '音频地址', type: 'url', required: true, placeholder: 'https://…/demo.wav', extra: '可直接上传或填写公网可访问直链' },
    ],
  },
  {
    category: 'text_embedding',
    label: '文本向量',
    hint: '输入一段文本，模型将编码为高维向量。',
    fields: [
      { key: 'input', label: '文本内容', type: 'textarea', rows: 4, required: true, placeholder: '例如：我喜欢在西湖边散步' },
    ],
  },
  {
    category: 'image_embedding',
    label: '图片向量',
    hint: '提供图片地址，模型将编码为高维向量。',
    fields: [
      { key: 'image_url', label: '图片地址', type: 'url', required: true, placeholder: 'https://…/example.jpg', extra: '可直接上传或填写公网可访问直链' },
    ],
  },
  {
    category: 'multimodal_embedding',
    label: '多模态向量',
    hint: '文本 / 图片 / 视频任意组合（至少填一项），一次调用返回各段内容的独立向量。',
    fields: [
      { key: 'text', label: '文本内容', type: 'textarea', rows: 3, placeholder: '例如：一只戴帽子的猫（文本/图片/视频至少填一项）' },
      { key: 'image_url', label: '图片地址', type: 'url', placeholder: 'https://…/example.jpg', extra: '可直接上传或填写公网可访问直链' },
      { key: 'video_url', label: '视频地址', type: 'url', placeholder: 'https://…/example.mp4', extra: '仅支持公网可访问的视频直链' },
    ],
  },
  {
    category: 'text_rerank',
    label: '文本重排',
    hint: '给出一段查询与候选文档，模型将按相关度排序打分。',
    fields: [
      { key: 'query', label: '查询', type: 'text', required: true, placeholder: '例如：苹果' },
      { key: 'documents', label: '候选文档', type: 'documents', rows: 5, required: true, placeholder: '每行一条，或用逗号分隔：\n苹果手机\n香蕉\n苹果汁', extra: '每行一条，提交时自动转为数组' },
    ],
  },
  {
    category: 'text_to_image',
    label: '文生图',
    hint: '输入提示词，模型将生成图片。',
    fields: [
      { key: 'prompt', label: '提示词', type: 'textarea', rows: 6, required: true, placeholder: '例如：一只戴帽子的橘猫，水彩风格' },
    ],
  },
  {
    category: 'text_to_audio',
    label: '文生音频',
    hint: '输入文字与可选音色，模型将合成为语音。',
    fields: [
      { key: 'text', label: '文本内容', type: 'textarea', rows: 5, required: true, placeholder: '例如：你好，欢迎体验智能语音合成' },
      { key: 'voice', label: '音色', type: 'voice', placeholder: '留空使用模型默认音色，例如：alloy / ziyao' },
    ],
  },
  {
    category: 'text_to_video',
    label: '文生视频',
    hint: '输入提示词，模型将生成视频（耗时较长）。',
    fields: [
      { key: 'prompt', label: '提示词', type: 'textarea', rows: 6, required: true, placeholder: '例如：一只猫在草地上行走，写实风格' },
    ],
  },
  {
    category: 'image_to_video',
    label: '图生视频',
    hint: '提供首帧图片与提示词，模型将生成动态视频。',
    fields: [
      { key: 'image_url', label: '首帧图片', type: 'url', required: true, placeholder: 'https://…/first_frame.jpg', extra: '可直接上传或填写公网可访问直链' },
      { key: 'prompt', label: '提示词', type: 'textarea', rows: 4, placeholder: '例如：镜头缓缓拉近，人物微笑' },
    ],
  },
];

// ==================== props / emit ====================

const props = withDefaults(
  defineProps<{
    category: string;
    supportsStream?: boolean;
    supportsThinking?: boolean;
    commonParams?: CommonParam[];
    /** 使用方正在执行调用（禁用控件 + 按钮 loading） */
    running?: boolean;
    /** 按钮文案 */
    submitText?: string;
    /** 是否显示面板内置结果区（体验页由外部 ResultPanel 渲染时传 false） */
    showResult?: boolean;
    /** 内置结果区展示：调用返回（success/message/latency_ms/data） */
    result?: any;
    resultError?: null | string;
  }>(),
  {
    supportsStream: false,
    supportsThinking: false,
    commonParams: () => [],
    running: false,
    submitText: '开始运行',
    showResult: false,
    result: undefined,
    resultError: null,
  },
);

const emit = defineEmits<{ run: [payload: TryPayload] }>();

// ==================== 状态 ====================

const form = reactive<Record<string, any>>({});
const useStream = ref(false);
const useThinking = ref(false);
/** 常用参数行：locked=true 来自模型登记的 common_params（名/类型固定），false 为用户临时添加 */
interface TryParamRow {
  name: string;
  type: CommonParamType;
  value: any;
  desc?: string;
  locked?: boolean;
}

const paramRows = ref<TryParamRow[]>([]);

const PARAM_TYPE_OPTIONS: { label: string; value: CommonParamType }[] = [
  { label: '布尔', value: 'boolean' },
  { label: '整数', value: 'integer' },
  { label: '浮点数', value: 'number' },
  { label: 'JSON', value: 'object' },
  { label: '字符串', value: 'string' },
];

const config = computed<TryFormConfig>(
  () => TYPE_FORMS.find((f) => f.category === props.category) || TYPE_FORMS[0]!,
);

/** 类型 → 控件初值（来自 default） */
function seedParamValue(type: CommonParamType, raw: any): any {
  switch (type) {
    case 'boolean': {
      return raw === true || raw === 'true' || raw === 1 || raw === '1';
    }
    case 'integer':
    case 'number': {
      const n = Number(raw);
      return Number.isNaN(n) ? undefined : n;
    }
    case 'object': {
      if (raw && typeof raw === 'object') return JSON.stringify(raw, null, 2);
      return raw ?? '';
    }
    default: {
      return raw ?? '';
    }
  }
}

/** 控件值 → 提交类型（按声明 type 转型；object 解析 JSON） */
function castParamValue(type: CommonParamType, val: any): any {
  switch (type) {
    case 'boolean': {
      return Boolean(val);
    }
    case 'integer': {
      const n = parseInt(String(val), 10);
      return Number.isNaN(n) ? undefined : n;
    }
    case 'number': {
      const n = Number(val);
      return Number.isNaN(n) ? undefined : n;
    }
    case 'object': {
      if (val && typeof val === 'object') return val;
      try {
        return JSON.parse(String(val));
      } catch {
        return undefined;
      }
    }
    default: {
      return val === '' ? undefined : String(val);
    }
  }
}

/** 切换参数类型时归一控件值，避免残留不匹配类型的旧值 */
function onParamTypeChange(row: TryParamRow) {
  row.value =
    row.type === 'boolean'
      ? false
      : row.type === 'object'
        ? '{}'
        : undefined;
}

function addParam() {
  paramRows.value.push({
    name: '',
    type: 'string',
    value: '',
    desc: '',
    locked: false,
  });
}
function removeParam(idx: number) {
  paramRows.value.splice(idx, 1);
}

// 切换模型（类型变化）：重置输入
watch(() => props.category, () => {
  Object.keys(form).forEach((k) => delete form[k]);
});
// 参数列表变化：以模型登记的 common_params 重建「锁定行」，保留用户临时添加行
watch(
  () => props.commonParams,
  (list) => {
    const locked: TryParamRow[] = (list || [])
      .filter((p) => p?.name)
      .map((p) => ({
        name: p.name,
        type: (p.type || 'string') as CommonParamType,
        value: seedParamValue((p.type || 'string') as CommonParamType, p.default),
        desc: p.desc || '',
        locked: true,
      }));
    const extras = paramRows.value.filter((r) => !r.locked);
    paramRows.value = [...locked, ...extras];
  },
  { immediate: true, deep: false },
);

// ==================== 上传 ====================

const uploadingKey = ref<string | null>(null);
const fileInputRef = ref<HTMLInputElement | null>(null);
let pendingUploadKey = '';

function triggerUpload(key: string) {
  pendingUploadKey = key;
  fileInputRef.value?.click();
}

async function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  uploadingKey.value = pendingUploadKey;
  try {
    const rep = await UploadFile(file);
    form[pendingUploadKey] = rep.url;
    message.success('上传成功，已回填文件地址');
  } catch (err: any) {
    message.error(err?.message || '上传失败');
  } finally {
    uploadingKey.value = null;
  }
}

// ==================== 提交 ====================

function buildInputs(): Record<string, any> {
  const inputs: Record<string, any> = {};
  for (const field of config.value.fields) {
    const raw = form[field.key];
    if (raw === undefined || raw === null || raw === '') continue;
    if (field.type === 'documents') {
      const docs = String(raw)
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (docs.length) inputs.documents = docs;
    } else {
      inputs[field.key] = String(raw).trim();
    }
  }
  return inputs;
}

function validate(): string | null {
  for (const field of config.value.fields) {
    if (!field.required) continue;
    const raw = form[field.key];
    if (raw === undefined || raw === null || String(raw).trim() === '') {
      return `请填写「${field.label}」`;
    }
  }
  return null;
}

function onSubmit() {
  const miss = validate();
  if (miss) {
    message.warning(miss);
    return;
  }
  const params: Record<string, any> = {};
  for (const row of paramRows.value) {
    const name = (row.name || '').trim();
    if (!name) continue;
    const casted = castParamValue(row.type, row.value);
    if (casted !== undefined) params[name] = casted;
  }
  emit('run', {
    inputs: buildInputs(),
    stream: props.supportsStream ? useStream.value : false,
    thinking: props.supportsThinking ? useThinking.value : false,
    params,
  });
}

/** 内置结果：内容文本 */
const resultText = computed(() => {
  const c = props.result?.data?.content;
  return typeof c === 'string' ? c : '';
});
const resultReasoning = computed(() => {
  const r = props.result?.data?.reasoning;
  return typeof r === 'string' ? r : '';
});
const resultUrls = computed<string[]>(() => props.result?.data?.urls || []);
</script>

<template>
  <div class="try-panel">
    <input
      ref="fileInputRef"
      type="file"
      style="display: none"
      @change="onFilePicked"
    />

    <!-- 流式 / 思考 开关 -->
    <div
      v-if="props.supportsStream || props.supportsThinking"
      class="switch-row"
    >
      <div v-if="props.supportsStream" class="switch-item">
        <span class="switch-label">开启流式输出</span>
        <a-switch v-model:checked="useStream" :disabled="props.running" size="small" />
      </div>
      <div v-if="props.supportsThinking" class="switch-item">
        <span class="switch-label">开启深度思考</span>
        <a-switch v-model:checked="useThinking" :disabled="props.running" size="small" />
      </div>
    </div>

    <!-- 常用参数：模型登记项可改值（覆盖默认）；可临时添加模型未登记的参数 -->
    <div class="param-block">
      <div class="param-title">常用参数</div>
      <div class="param-head">
        <span class="col-name">参数名</span>
        <span class="col-type">类型</span>
        <span class="col-val">值</span>
        <span class="col-desc">说明</span>
        <span class="col-op"></span>
      </div>
      <div v-for="(row, i) in paramRows" :key="i" class="param-row">
        <span class="col-name">
          <span v-if="row.locked" :title="row.name">{{ row.name }}</span>
          <a-input
            v-else
            v-model:value="row.name"
            size="small"
            :maxlength="64"
            placeholder="参数名"
            :disabled="props.running"
          />
        </span>
        <span class="col-type">
          <a-tag v-if="row.locked">{{ row.type }}</a-tag>
          <a-select
            v-else
            v-model:value="row.type"
            size="small"
            :options="PARAM_TYPE_OPTIONS"
            :disabled="props.running"
            style="width: 100%"
            @change="onParamTypeChange(row)"
          />
        </span>
        <span class="col-val">
          <a-switch
            v-if="row.type === 'boolean'"
            v-model:checked="row.value"
            size="small"
            :disabled="props.running"
          />
          <a-input-number
            v-else-if="row.type === 'integer' || row.type === 'number'"
            v-model:value="row.value"
            size="small"
            :precision="row.type === 'integer' ? 0 : undefined"
            :disabled="props.running"
            style="width: 100%"
          />
          <a-textarea
            v-else-if="row.type === 'object'"
            v-model:value="row.value"
            :auto-size="{ minRows: 1, maxRows: 3 }"
            :disabled="props.running"
            placeholder="JSON"
          />
          <a-input
            v-else
            v-model:value="row.value"
            size="small"
            :disabled="props.running"
          />
        </span>
        <span class="col-desc">
          <span v-if="row.locked" :title="row.desc">{{ row.desc || '-' }}</span>
          <a-input
            v-else
            v-model:value="row.desc"
            size="small"
            :maxlength="255"
            placeholder="说明"
            :disabled="props.running"
          />
        </span>
        <span class="col-op">
          <a-button
            v-if="!row.locked"
            type="text"
            danger
            size="small"
            :disabled="props.running"
            @click="removeParam(i)"
          >
            <template #icon>
              <DeleteOutlined />
            </template>
          </a-button>
        </span>
      </div>
      <div class="param-add">
        <a-button
          type="dashed"
          size="small"
          block
          :disabled="props.running"
          @click="addParam"
        >
          <template #icon>
            <PlusOutlined />
          </template>
          添加参数
        </a-button>
      </div>
    </div>

    <!-- 类型输入区 -->
    <div class="form-hint">
      <ThunderboltOutlined class="hint-icon" />
      <span>{{ config.hint }}</span>
    </div>
    <a-form layout="vertical" class="form-fields">
      <template v-for="field in config.fields" :key="field.key">
        <a-form-item :label="field.label" :required="field.required">
          <a-textarea
            v-if="field.type === 'textarea' || field.type === 'documents'"
            v-model:value="form[field.key]"
            :rows="field.rows"
            :placeholder="field.placeholder"
            :disabled="props.running"
            allow-clear
          />
          <div v-else-if="field.type === 'url'" class="url-line">
            <a-input
              v-model:value="form[field.key]"
              :placeholder="field.placeholder"
              :disabled="props.running"
              allow-clear
            >
              <template #prefix>
                <span class="url-prefix">URL</span>
              </template>
            </a-input>
            <a-button
              size="small"
              :loading="uploadingKey === field.key"
              :disabled="props.running"
              @click="triggerUpload(field.key)"
            >
              <template #icon>
                <LoadingOutlined v-if="uploadingKey === field.key" />
                <UploadOutlined v-else />
              </template>
              上传
            </a-button>
          </div>
          <a-input
            v-else
            v-model:value="form[field.key]"
            :placeholder="field.placeholder"
            :disabled="props.running"
            allow-clear
          />
          <div v-if="field.extra" class="field-extra">{{ field.extra }}</div>
        </a-form-item>
      </template>
    </a-form>

    <div class="form-actions">
      <a-button
        type="primary"
        :loading="props.running"
        :disabled="props.running || !props.category"
        @click="onSubmit"
      >
        <template #icon>
          <SendOutlined />
        </template>
        {{ props.submitText }}
      </a-button>
    </div>

    <!-- 内置结果区（供测试弹窗使用） -->
    <div v-if="props.showResult" class="try-result">
      <a-divider style="margin: 12px 0 8px" />
      <a-alert
        v-if="props.resultError"
        type="error"
        show-icon
        :message="props.resultError"
      />
      <template v-else-if="props.result">
        <a-alert
          :type="props.result.success ? 'success' : 'error'"
          show-icon
          :message="props.result.message"
          style="margin-bottom: 8px"
        />
        <div v-if="resultReasoning" class="try-reason">
          <div class="try-reason-title">思考过程</div>
          <pre class="try-pre">{{ resultReasoning }}</pre>
        </div>
        <pre v-if="resultText" class="try-pre">{{ resultText }}</pre>
        <div v-if="resultUrls.length" class="try-urls">
          <div v-for="(u, i) in resultUrls" :key="i" class="try-url-item">
            <img v-if="u.startsWith('data:image') || /\.(png|jpe?g|webp|gif)/i.test(u)" :src="u" class="try-img" />
            <video v-else-if="/\.(mp4|webm|mov)/i.test(u)" :src="u" controls class="try-video" />
            <audio v-else-if="/\.(mp3|wav|ogg)|data:audio/i.test(u)" :src="u" controls />
            <a v-else :href="u" target="_blank">{{ u }}</a>
          </div>
        </div>
        <div v-if="props.result.data?.vectors" class="try-vec">
          向量维度：{{ props.result.data.vectors.dim }} · 条数：{{ props.result.data.vectors.count }}
        </div>
        <div v-if="props.result.data?.scores" class="try-vec">
          重排结果：{{ JSON.stringify(props.result.data.scores) }}
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.try-panel {
  display: flex;
  flex-direction: column;
}

.switch-row {
  display: flex;
  gap: 24px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fafafa;
  border-radius: 6px;
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-label {
  font-size: 13px;
  color: #595959;
}

.param-block {
  margin-bottom: 14px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  overflow: hidden;
}

.param-title {
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: #1f1f1f;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.param-head,
.param-row {
  display: grid;
  grid-template-columns: 1.2fr 0.9fr 1.4fr 1.2fr 32px;
  gap: 8px;
  align-items: center;
  padding: 6px 12px;
  font-size: 12px;
}

.param-head {
  color: #8c8c8c;
  background: #fcfcfc;
  border-bottom: 1px solid #f5f5f5;
}

.param-row {
  border-bottom: 1px solid #fafafa;
}

.param-row:last-child {
  border-bottom: none;
}

.col-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}

.col-op {
  text-align: center;
}

.param-add {
  padding: 8px 12px 10px;
}

.form-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  font-size: 13px;
  color: #595959;
  background: #fafafa;
  border-left: 3px solid #1677ff;
  border-radius: 4px;
  line-height: 1.6;
}

.hint-icon {
  margin-top: 2px;
  color: #1677ff;
}

.form-fields {
  flex: 1;
}

.url-line {
  display: flex;
  gap: 8px;
  align-items: center;
}

.url-line .ant-input-affix-wrapper,
.url-line :deep(.ant-input-affix-wrapper) {
  flex: 1;
}

.field-extra {
  margin-top: 4px;
  font-size: 12px;
  color: #bfbfbf;
}

.url-prefix {
  font-size: 11px;
  font-weight: 600;
  color: #1677ff;
  margin-right: 2px;
}

.form-actions {
  padding-top: 14px;
  border-top: 1px dashed #f0f0f0;
  text-align: right;
}

.try-result {
  margin-top: 4px;
}

.try-pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  max-height: 280px;
  overflow-y: auto;
}

.try-reason {
  margin-bottom: 8px;
}

.try-reason-title {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 4px;
}

.try-urls {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.try-img {
  max-width: 100%;
  border-radius: 6px;
}

.try-video {
  max-width: 100%;
  border-radius: 6px;
}

.try-vec {
  margin-top: 8px;
  font-size: 12px;
  color: #595959;
}
</style>
