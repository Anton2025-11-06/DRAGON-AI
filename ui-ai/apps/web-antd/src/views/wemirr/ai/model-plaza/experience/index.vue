<script setup lang="ts">
/**
 * 模型体验 - 主页面：左侧选择已授权模型，右侧填写参数并实时查看输出。
 * 无会话概念：每次提交独立调用网关 /api/model；
 * 文本类（文生文/图片理解/视频理解）走 SSE 流式，其余类型一次返回。
 */
import type { MyKeyRep } from '../api';
import { GetMyKeys } from '../api';

import { computed, onMounted, ref } from 'vue';

import {
  ApiOutlined,
  ExperimentOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { invokeModel, parseExperienceResult } from './api';
import type { ModelExperienceBody, ModelExperienceResult } from './api';
import ModelPicker from './components/ModelPicker.vue';
import ResultPanel from './components/ResultPanel.vue';
import ModelTryPanel from '../components/ModelTryPanel.vue';

defineOptions({ name: 'ModelExperience' });

const models = ref<MyKeyRep[]>([]);
const loadingKeys = ref(false);
const selectedApplyId = ref<number | null>(null);

const selectedModel = computed(
  () => models.value.find((m) => m.apply_id === selectedApplyId.value) || null,
);

const result = ref<ModelExperienceResult | null>(null);
const error = ref<string | null>(null);
const running = ref(false);
const streaming = ref(false);
const elapsedMs = ref<number | null>(null);

async function loadKeys() {
  loadingKeys.value = true;
  try {
    const list = await GetMyKeys();
    models.value = list || [];
    if (models.value.length && selectedApplyId.value === null) {
      const first = models.value[0];
      if (first) selectedApplyId.value = first.apply_id;
    }
  } catch (e: any) {
    message.error(e?.message || '加载已授权模型失败');
  } finally {
    loadingKeys.value = false;
  }
}

/** 从网关错误体中提取可读文案（422 校验 / OpenAI error / detail） */
function extractErrorMessage(e: any): string {
  const data = e?.responseData;
  if (data && typeof data === 'object') {
    const detail = data.detail ?? data.error?.message;
    if (typeof detail === 'string') return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  }
  if (typeof data === 'string' && data.trim()) return data.slice(0, 500);
  return e?.message || '调用失败，请稍后重试';
}

async function onTryRun(payload: {
  inputs: Record<string, any>;
  params: Record<string, any>;
  stream: boolean;
  thinking: boolean;
}) {
  const m = selectedModel.value;
  if (!m) return;
  result.value = null;
  error.value = null;
  elapsedMs.value = null;
  running.value = true;
  streaming.value = payload.stream;
  const t0 = performance.now();
  // 输入体 + 常用参数 + 思考开关合并为网关 body；流式开关按面板（而非硬编码类型）
  const body = {
    ...payload.inputs,
    ...payload.params,
    ...(payload.thinking ? { thinking: true } : {}),
    stream: payload.stream || undefined,
  } as ModelExperienceBody;
  const isStream = payload.stream;
  let streamText = '';
  let streamReasoning = '';
  try {
    const { raw, usage } = await invokeModel(
      {
        apiKey: m.api_key,
        model: m.model_name,
        body,
      },
      (chunk) => {
        if (chunk.content) streamText += chunk.content;
        if (chunk.reasoningContent) streamReasoning += chunk.reasoningContent;
        if (chunk.done) {
          streaming.value = false;
          elapsedMs.value = Math.round(performance.now() - t0);
        }
      },
    );
    const parsed = parseExperienceResult(m.category, raw);
    // 流式：SSE 已拆块累积，用累积文本覆盖一次性解析结果
    result.value = isStream
      ? {
          ...parsed,
          text: streamText || parsed.text,
          reasoning: streamReasoning || parsed.reasoning,
          usage: usage || parsed.usage,
        }
      : parsed;
    elapsedMs.value = Math.round(performance.now() - t0);
  } catch (e: any) {
    if (e?.name === 'AbortError') return;
    error.value = extractErrorMessage(e);
  } finally {
    running.value = false;
    streaming.value = false;
  }
}

onMounted(loadKeys);
</script>

<template>
  <div class="experience-page">
    <!-- 顶栏 -->
    <div class="page-header">
      <div class="header-left">
        <ExperimentOutlined class="header-icon" />
        <span class="header-title">模型体验</span>
        <span class="header-sub">使用已授权模型，体验 12 类 AI 能力</span>
      </div>
      <a-button size="small" :loading="loadingKeys" @click="loadKeys">
        <ReloadOutlined />
        刷新列表
      </a-button>
    </div>

    <div class="page-body">
      <!-- 左侧：模型选择 -->
      <aside class="model-side">
        <div class="side-title">
          <span>已授权模型</span>
          <span class="side-count">{{ models.length }}</span>
        </div>
        <ModelPicker
          v-model:selected-apply-id="selectedApplyId"
          :models="models"
          :loading="loadingKeys"
        />
      </aside>

      <!-- 右侧：体验台 -->
      <main class="stage">
        <!-- 模型信息条 -->
        <div v-if="selectedModel" class="model-bar">
          <div class="model-bar-main">
            <span class="model-name">{{ selectedModel.name }}</span>
            <a-tag color="blue">{{ selectedModel.category_label }}</a-tag>
            <a-tag>{{ selectedModel.provider_label }}</a-tag>
          </div>
          <div class="model-bar-meta">
            <ApiOutlined />
            <span class="meta-text">{{ selectedModel.model_name }}</span>
            <span v-if="selectedModel.gateway_url" class="meta-divider">·</span>
            <span v-if="selectedModel.gateway_url" class="meta-text">
              {{ selectedModel.gateway_url }}
            </span>
          </div>
        </div>
        <div v-else class="model-bar model-bar-empty">
          <span>请在左侧选择要体验的模型</span>
        </div>

        <div class="stage-grid">
          <!-- 表单区 -->
          <section class="stage-panel form-panel">
            <ModelTryPanel
              v-if="selectedModel"
              :category="selectedModel.category"
              :supports-stream="!!selectedModel.supports_stream"
              :supports-thinking="!!selectedModel.supports_thinking"
              :common-params="selectedModel.common_params || []"
              :running="running"
              submit-text="开始体验"
              @run="onTryRun"
            />
            <div v-else class="placeholder">
              <ExperimentOutlined class="placeholder-icon" />
              <p>选择模型后开始体验</p>
            </div>
          </section>

          <!-- 结果区 -->
          <section class="stage-panel result-panel-wrap">
            <ResultPanel
              :result="result"
              :error="error"
              :loading="running"
              :streaming="streaming"
              :elapsed-ms="elapsedMs"
            />
          </section>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.experience-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ===== 顶栏 ===== */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-icon {
  font-size: 18px;
  color: #1677ff;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f1f1f;
}

.header-sub {
  font-size: 12px;
  color: #8c8c8c;
}

/* ===== 主体 ===== */
.page-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.model-side {
  display: flex;
  flex-direction: column;
  width: 300px;
  flex-shrink: 0;
  border-right: 1px solid #f0f0f0;
  background: #fafafa;
}

.side-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 0;
  font-size: 13px;
  font-weight: 600;
  color: #595959;
}

.side-count {
  padding: 0 8px;
  font-size: 12px;
  font-weight: 500;
  color: #1677ff;
  background: #e6f4ff;
  border-radius: 10px;
}

.stage {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  background: #f5f7fa;
}

/* ===== 模型信息条 ===== */
.model-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
}

.model-bar-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.model-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f1f1f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-bar-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: #8c8c8c;
  font-size: 12px;
}

.meta-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}

.meta-divider {
  color: #d9d9d9;
}

.model-bar-empty {
  justify-content: flex-start;
  color: #8c8c8c;
  font-size: 13px;
}

/* ===== 表单 + 结果 ===== */
.stage-grid {
  display: grid;
  grid-template-columns: minmax(360px, 2fr) minmax(0, 3fr);
  flex: 1;
  min-height: 0;
  gap: 14px;
  padding: 14px 16px;
}

.stage-panel {
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  padding: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.form-panel {
  overflow-y: auto;
}

.result-panel-wrap {
  overflow: hidden;
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #bfbfbf;
}

.placeholder-icon {
  font-size: 40px;
  margin-bottom: 10px;
}

@media (max-width: 1100px) {
  .stage-grid {
    grid-template-columns: 1fr;
    overflow-y: auto;
  }
}
</style>