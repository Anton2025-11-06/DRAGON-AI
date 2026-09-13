<script setup lang="ts">
/**
 * 模型体验 - 类型表单：按 12 能力类型配置驱动渲染输入区。
 * body 字段对齐后端 common_model.entry.body_to_kwargs（prompt/input/messages/image_url/…），
 * documents 支持逗号/换行分隔，提交时自动转换为数组。
 */
import type { ModelExperienceBody } from '../api';

import { reactive, watch } from 'vue';

import { message } from 'ant-design-vue';

import { SendOutlined, ThunderboltOutlined } from '@ant-design/icons-vue';

// ==================== 表单字段/配置类型 ====================

export interface TypeField {
  key: keyof ModelExperienceBody;
  label: string;
  type: 'text' | 'textarea' | 'url' | 'voice' | 'documents';
  placeholder: string;
  required?: boolean;
  rows?: number;
  extra?: string;
}

export interface TypeFormConfig {
  category: string;
  label: string;
  hint: string;
  fields: TypeField[];
}

// ==================== 12 类型表单配置 ====================
// 仅供本组件内部渲染使用，无需导出（script setup 不允许 module exports）

const TYPE_FORMS: TypeFormConfig[] = [
  {
    category: 'text_to_text',
    label: '文生文',
    hint: '输入提示词，模型将流式输出回复（支持深度思考）。',
    fields: [
      {
        key: 'prompt',
        label: '提示词',
        type: 'textarea',
        rows: 6,
        required: true,
        placeholder: '例如：用 80 字介绍杭州西湖的四季…',
      },
    ],
  },
  {
    category: 'image_understand',
    label: '图片理解',
    hint: '提供图片公网地址与提问，模型将基于图片内容作答（流式）。',
    fields: [
      {
        key: 'image_url',
        label: '图片地址',
        type: 'url',
        required: true,
        placeholder: 'https://…/example.jpg',
        extra: '需为公网可访问的图片直链',
      },
      {
        key: 'prompt',
        label: '提问',
        type: 'textarea',
        rows: 4,
        required: true,
        placeholder: '例如：图里有什么？描述一下画面细节',
      },
    ],
  },
  {
    category: 'video_understand',
    label: '视频理解',
    hint: '提供视频公网地址与提问，模型将基于视频内容作答（流式）。',
    fields: [
      {
        key: 'video_url',
        label: '视频地址',
        type: 'url',
        required: true,
        placeholder: 'https://…/example.mp4',
        extra: '需为公网可访问的视频直链',
      },
      {
        key: 'prompt',
        label: '提问',
        type: 'textarea',
        rows: 4,
        required: true,
        placeholder: '例如：视频里发生了什么？',
      },
    ],
  },
  {
    category: 'ocr',
    label: 'OCR 文字识别',
    hint: '提供图片公网地址，模型将识别并输出图中的全部文字。',
    fields: [
      {
        key: 'image_url',
        label: '图片地址',
        type: 'url',
        required: true,
        placeholder: 'https://…/scan.png',
        extra: '需为公网可访问的图片直链',
      },
    ],
  },
  {
    category: 'audio_to_text',
    label: '音频转文字',
    hint: '提供音频公网地址，模型将转写为文字。',
    fields: [
      {
        key: 'audio_url',
        label: '音频地址',
        type: 'url',
        required: true,
        placeholder: 'https://…/demo.wav',
        extra: '需为公网可访问的音频直链',
      },
    ],
  },
  {
    category: 'text_embedding',
    label: '文本向量',
    hint: '输入一段文本，模型将编码为高维向量。',
    fields: [
      {
        key: 'input',
        label: '文本内容',
        type: 'textarea',
        rows: 4,
        required: true,
        placeholder: '例如：我喜欢在西湖边散步',
      },
    ],
  },
  {
    category: 'image_embedding',
    label: '图片向量',
    hint: '提供图片公网地址，模型将编码为高维向量。',
    fields: [
      {
        key: 'image_url',
        label: '图片地址',
        type: 'url',
        required: true,
        placeholder: 'https://…/example.jpg',
        extra: '需为公网可访问的图片直链',
      },
    ],
  },
  {
    category: 'text_rerank',
    label: '文本重排',
    hint: '给出一段查询与候选文档，模型将按相关度排序打分。',
    fields: [
      {
        key: 'query',
        label: '查询',
        type: 'text',
        required: true,
        placeholder: '例如：苹果',
      },
      {
        key: 'documents',
        label: '候选文档',
        type: 'documents',
        rows: 5,
        required: true,
        placeholder: '每行一条，或用逗号分隔：\n苹果手机\n香蕉\n苹果汁',
        extra: '每行一条，提交时自动转为数组',
      },
    ],
  },
  {
    category: 'text_to_image',
    label: '文生图',
    hint: '输入提示词，模型将生成图片（按异步任务轮询）。',
    fields: [
      {
        key: 'prompt',
        label: '提示词',
        type: 'textarea',
        rows: 6,
        required: true,
        placeholder: '例如：一只戴帽子的橘猫，水彩风格',
      },
    ],
  },
  {
    category: 'text_to_audio',
    label: '文生音频',
    hint: '输入文字与可选音色，模型将合成为语音（自动播放）。',
    fields: [
      {
        key: 'text',
        label: '文本内容',
        type: 'textarea',
        rows: 5,
        required: true,
        placeholder: '例如：你好，欢迎体验智能语音合成',
      },
      {
        key: 'voice',
        label: '音色',
        type: 'voice',
        placeholder: '留空使用模型默认音色，例如：alloy / ziyao',
      },
    ],
  },
  {
    category: 'text_to_video',
    label: '文生视频',
    hint: '输入提示词，模型将生成视频（按异步任务轮询，耗时较长）。',
    fields: [
      {
        key: 'prompt',
        label: '提示词',
        type: 'textarea',
        rows: 6,
        required: true,
        placeholder: '例如：一只猫在草地上行走，写实风格',
      },
    ],
  },
  {
    category: 'image_to_video',
    label: '图生视频',
    hint: '提供首帧图片与提示词，模型将生成动态视频（按异步任务轮询）。',
    fields: [
      {
        key: 'image_url',
        label: '首帧图片',
        type: 'url',
        required: true,
        placeholder: 'https://…/first_frame.jpg',
        extra: '需为公网可访问的图片直链',
      },
      {
        key: 'prompt',
        label: '提示词',
        type: 'textarea',
        rows: 4,
        placeholder: '例如：镜头缓缓拉近，人物微笑',
      },
    ],
  },
];

const props = defineProps<{
  category: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  submit: [body: ModelExperienceBody];
}>();

const form = reactive<Record<string, any>>({});

/** 当前类型表单配置（按 category 匹配，默认取第一个） */
const config = TYPE_FORMS.find((f) => f.category === props.category) || TYPE_FORMS[0]!;

// 切换模型（类型变化）时重置表单
watch(
  () => props.category,
  () => {
    Object.keys(form).forEach((k) => delete form[k]);
  },
);

/** 组装网关 body：documents 逗号/换行分隔转数组，空字段剔除 */
function buildBody(): ModelExperienceBody {
  const body: ModelExperienceBody = {};
  for (const field of config.fields) {
    const raw = form[field.key];
    if (raw === undefined || raw === null || raw === '') continue;
    if (field.type === 'documents') {
      const docs = String(raw)
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (docs.length) body.documents = docs;
    } else {
      body[field.key] = String(raw).trim() as never;
    }
  }
  return body;
}

/** 校验必填（返回缺失 label 提示） */
function validate(): string | null {
  for (const field of config.fields) {
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
  emit('submit', buildBody());
}
</script>

<template>
  <div class="type-form">
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
            :disabled="disabled"
            allow-clear
          />
          <a-input
            v-else-if="field.type === 'url'"
            v-model:value="form[field.key]"
            :placeholder="field.placeholder"
            :disabled="disabled"
            allow-clear
          >
            <template #prefix>
              <span class="url-prefix">URL</span>
            </template>
          </a-input>
          <a-input
            v-else
            v-model:value="form[field.key]"
            :placeholder="field.placeholder"
            :disabled="disabled"
            allow-clear
          />
          <div v-if="field.extra" class="field-extra">{{ field.extra }}</div>
        </a-form-item>
      </template>
    </a-form>
    <div class="form-actions">
      <a-button
        type="primary"
        size="large"
        :loading="disabled"
        :disabled="disabled || !props.category"
        @click="onSubmit"
      >
        <template #icon>
          <SendOutlined />
        </template>
        开始体验
      </a-button>
    </div>
  </div>
</template>

<style scoped>
.type-form {
  display: flex;
  flex-direction: column;
  height: 100%;
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
  overflow-y: auto;
  padding: 0 2px;
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
</style>