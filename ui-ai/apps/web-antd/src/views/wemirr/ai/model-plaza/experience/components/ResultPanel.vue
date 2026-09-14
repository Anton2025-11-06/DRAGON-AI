<script setup lang="ts">
/**
 * 模型体验 - 结果面板：按类型渲染体验结果。
 * - 文本：气泡展示（流式由父级累积）+ 推理过程折叠
 * - 图像/音频/视频：媒体预览（音频/视频自动播放，兼容 data URI 与 http 直链）
 * - 向量：维度 + 前 8 维样例；重排：index/score 全列表格
 * - 底部：usage / 耗时元信息
 */
import type { ModelExperienceResult } from '../api';

import { computed, ref } from 'vue';

import {
  ClockCircleOutlined,
  CopyOutlined,
  DownOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

const props = defineProps<{
  result: ModelExperienceResult | null;
  error: string | null;
  loading?: boolean;
  streaming?: boolean;
  elapsedMs?: number | null;
}>();

/** 是否有可展示内容 */
const hasContent = computed(
  () =>
    !!props.result?.text ||
    !!props.result?.reasoning ||
    (props.result?.urls?.length || 0) > 0 ||
    !!props.result?.vectors ||
    !!props.result?.scores,
);

const showReasoning = ref(false);

/** 推理折叠展开态变化时同步标题（active-key 由内容点击驱动） */
function onReasoningChange(keys: string[] | string) {
  showReasoning.value = Array.isArray(keys) ? keys.includes('r') : keys === 'r';
}

// ==================== URL 分类 ====================

type MediaKind = 'image' | 'audio' | 'video' | 'link';

function classifyUrl(url: string): MediaKind {
  const lower = url.toLowerCase();
  if (lower.startsWith('data:image')) return 'image';
  if (lower.startsWith('data:audio')) return 'audio';
  if (lower.startsWith('data:video')) return 'video';
  const m = (lower.split('?')[0] ?? '').match(/\.([a-z0-9]+)$/);
  const ext = m ? (m[1] ?? '') : '';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'avif'].includes(ext)) return 'image';
  if (['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'opus'].includes(ext)) return 'audio';
  if (['mp4', 'webm', 'mov', 'm3u8', 'mkv'].includes(ext)) return 'video';
  return 'link';
}

const imageUrls = computed(() => (props.result?.urls || []).filter((u) => classifyUrl(u) === 'image'));
const audioUrls = computed(() => (props.result?.urls || []).filter((u) => classifyUrl(u) === 'audio'));
const videoUrls = computed(() => (props.result?.urls || []).filter((u) => classifyUrl(u) === 'video'));
const linkUrls = computed(() => (props.result?.urls || []).filter((u) => classifyUrl(u) === 'link'));

/** 复制 URL 到剪贴板 */
async function copyText(text: string, label = '已复制') {
  try {
    await navigator.clipboard.writeText(text);
    message.success(label);
  } catch {
    message.warning('复制失败，请手动选择复制');
  }
}

/** data URI 截断显示 */
function shortUrl(url: string, max = 60): string {
  if (url.startsWith('data:')) {
    const head = url.slice(0, max);
    return `${head}…（${(url.length * 0.75 / 1024).toFixed(1)} KB）`;
  }
  return url.length > max ? `${url.slice(0, max)}…` : url;
}

/** 原始响应格式化（异常 / 未识别数据直出展示） */
function formatRaw(raw: any): string {
  if (raw == null) return '（无响应内容）';
  if (typeof raw === 'string') return raw;
  try {
    return JSON.stringify(raw, null, 2);
  } catch {
    return String(raw);
  }
}

const usageText = computed(() => {
  const u = props.result?.usage;
  if (!u || typeof u !== 'object') return '';
  const parts: string[] = [];
  if (u.prompt_tokens != null) parts.push(`输入 ${u.prompt_tokens} tokens`);
  if (u.completion_tokens != null) parts.push(`输出 ${u.completion_tokens} tokens`);
  if (u.total_tokens != null) parts.push(`合计 ${u.total_tokens} tokens`);
  return parts.join(' · ');
});
</script>

<template>
  <div class="result-panel">
    <!-- ① 异常 / 错误：置顶醒目（含网关返回的异常数据） -->
    <div v-if="error" class="error-state">
      <div class="error-title">调用失败</div>
      <pre class="error-body">{{ error }}</pre>
    </div>

    <!-- ② 加载态 -->
    <div v-else-if="loading && !result" class="loading-state">
      <a-spin size="large" />
      <p class="loading-tip">正在调用模型{{ streaming ? '（流式输出中）' : '…' }}</p>
    </div>

    <!-- ③ 空态 -->
    <div v-else-if="!result" class="empty-state">
      <RobotOutlined class="empty-icon" />
      <p class="empty-title">选择左侧模型，填写参数开始体验</p>
      <p class="empty-tip">结果将在这里展示（文本 / 图片 / 音频 / 视频 / 向量 / 重排）</p>
    </div>

    <!-- 结果区 -->
    <div v-else-if="result && hasContent" class="result-body">
      <!-- 流式文本 -->
      <div v-if="result.text" class="text-block">
        <div class="block-head">
          <span class="block-title">输出</span>
          <span v-if="streaming" class="streaming-badge">生成中…</span>
          <span v-if="elapsedMs != null" class="elapsed-badge">
            <ClockCircleOutlined /> {{ elapsedMs }} ms
          </span>
        </div>
        <div class="text-bubble">{{ result.text }}</div>
      </div>

      <!-- 推理过程 -->
      <a-collapse
        v-if="result.reasoning"
        :active-key="showReasoning ? ['r'] : []"
        class="reasoning-collapse"
        ghost
        @change="onReasoningChange"
      >
        <a-collapse-panel
          key="r"
          :header="showReasoning ? '收起推理过程' : '查看推理过程'"
        >
          <pre class="reasoning-body">{{ result.reasoning }}</pre>
        </a-collapse-panel>
      </a-collapse>

      <!-- 向量 -->
      <div v-if="result.vectors" class="section-card">
        <div class="block-head">
          <span class="block-title">向量结果</span>
        </div>
        <div class="vector-meta">
          <a-tag color="blue">维度 {{ result.vectors.dim }}</a-tag>
          <a-tag color="cyan">共 {{ result.vectors.count }} 条</a-tag>
        </div>
        <div class="vector-sample">
          <span class="sample-label">首条前 8 维：</span>
          <code class="sample-code">{{ JSON.stringify(result.vectors.sample) }}</code>
          <a-button
            type="link"
            size="small"
            class="copy-btn"
            @click="copyText(JSON.stringify(result.vectors!.sample), '向量已复制')"
          >
            <CopyOutlined /> 复制
          </a-button>
        </div>
      </div>

      <!-- 重排 -->
      <div v-if="result.scores" class="section-card">
        <div class="block-head">
          <span class="block-title">重排结果</span>
          <a-tag color="gold">{{ result.scores.length }} 条</a-tag>
        </div>
        <a-table
          :data-source="result.scores"
          :columns="[
            { title: '序号', dataIndex: 'index', width: 80 },
            { title: '相关度得分', dataIndex: 'relevance_score', key: 'score' },
          ]"
          :pagination="false"
          size="small"
          row-key="index"
        />
      </div>

      <!-- 图片 -->
      <div v-if="imageUrls.length" class="section-card">
        <div class="block-head">
          <span class="block-title">生成图片</span>
          <span class="count-badge">{{ imageUrls.length }} 张</span>
        </div>
        <div class="image-grid">
          <div v-for="(url, i) in imageUrls" :key="i" class="image-item">
            <img :src="url" alt="生成图片" class="preview-img" />
            <div class="url-line">
              <span class="url-text" :title="url">{{ shortUrl(url) }}</span>
              <a-button
                type="link"
                size="small"
                class="copy-btn"
                @click="copyText(url)"
              >
                <CopyOutlined />
              </a-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 音频 -->
      <div v-if="audioUrls.length" class="section-card">
        <div class="block-head">
          <span class="block-title">生成音频</span>
          <span class="count-badge">{{ audioUrls.length }} 段</span>
        </div>
        <div v-for="(url, i) in audioUrls" :key="i" class="media-item">
          <audio :src="url" controls autoplay class="media-player" />
          <div class="url-line">
            <span class="url-text" :title="url">{{ shortUrl(url) }}</span>
            <a-button type="link" size="small" class="copy-btn" @click="copyText(url)">
              <CopyOutlined />
            </a-button>
          </div>
        </div>
      </div>

      <!-- 视频 -->
      <div v-if="videoUrls.length" class="section-card">
        <div class="block-head">
          <span class="block-title">生成视频</span>
          <span class="count-badge">{{ videoUrls.length }} 段</span>
        </div>
        <div v-for="(url, i) in videoUrls" :key="i" class="media-item">
          <video :src="url" controls autoplay muted class="media-player" />
          <div class="url-line">
            <span class="url-text" :title="url">{{ shortUrl(url) }}</span>
            <a-button type="link" size="small" class="copy-btn" @click="copyText(url)">
              <CopyOutlined />
            </a-button>
          </div>
        </div>
      </div>

      <!-- 其他链接 -->
      <div v-if="linkUrls.length" class="section-card">
        <div class="block-head">
          <span class="block-title">产物链接</span>
        </div>
        <div v-for="(url, i) in linkUrls" :key="i" class="url-line">
          <a :href="url" target="_blank" rel="noopener" class="url-text" :title="url">
            {{ shortUrl(url) }}
          </a>
          <a-button type="link" size="small" class="copy-btn" @click="copyText(url)">
            <CopyOutlined />
          </a-button>
        </div>
      </div>

      <!-- 元信息 -->
      <div
        v-if="usageText || elapsedMs != null || !streaming"
        class="meta-bar"
      >
        <ThunderboltOutlined v-if="usageText" class="meta-icon" />
        <span v-if="usageText">{{ usageText }}</span>
        <span v-if="usageText && elapsedMs != null" class="meta-sep">·</span>
        <span v-if="elapsedMs != null">耗时 {{ elapsedMs }} ms</span>
        <a-button
          v-if="result.raw"
          type="link"
          size="small"
          class="raw-btn"
          @click="copyText(JSON.stringify(result.raw), '原始响应已复制')"
        >
          <DownOutlined /> 复制原始响应
        </a-button>
      </div>
    </div>

    <!-- 异常数据 / 未识别结果格式：原始响应直出 -->
    <div v-else-if="result && !hasContent" class="result-body">
      <div class="block-head">
        <span class="block-title">原始响应</span>
        <a-tag color="orange">未识别为已知结果格式</a-tag>
      </div>
      <pre class="raw-body">{{ formatRaw(result.raw) }}</pre>
      <div class="meta-bar">
        <a-button
          type="link"
          size="small"
          class="raw-btn"
          @click="copyText(formatRaw(result.raw), '原始响应已复制')"
        >
          <CopyOutlined /> 复制原始响应
        </a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-panel {
  height: 100%;
  overflow-y: auto;
  padding: 4px 2px;
}

/* ===== 空态 / 加载 / 错误 ===== */
.empty-state,
.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 240px;
  color: #8c8c8c;
  text-align: center;
}

.empty-icon {
  font-size: 40px;
  color: #d9d9d9;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 14px;
  color: #595959;
  margin-bottom: 6px;
}

.empty-tip,
.loading-tip {
  font-size: 12px;
  color: #bfbfbf;
}

.loading-tip {
  margin-top: 12px;
}

.error-state {
  /* 覆盖共用的居中规则：异常数据置顶、靠左、满宽，醒目不碍眼 */
  align-items: stretch;
  justify-content: flex-start;
  height: auto;
  min-height: auto;
  padding: 16px;
  text-align: left;
}

.error-title {
  font-size: 14px;
  font-weight: 600;
  color: #cf1322;
  margin-bottom: 10px;
}

.error-body {
  max-width: 100%;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.6;
  color: #a8071a;
  background: #fff1f0;
  border: 1px solid #ffa39e;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  text-align: left;
}

.raw-body {
  max-width: 100%;
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.6;
  color: #614700;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  text-align: left;
}

/* ===== 结果区 ===== */
.result-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.block-title {
  font-size: 13px;
  font-weight: 600;
  color: #1f1f1f;
}

.streaming-badge {
  padding: 1px 8px;
  font-size: 11px;
  color: #1677ff;
  background: #e6f4ff;
  border-radius: 10px;
  animation: pulse 1.2s infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

.elapsed-badge,
.count-badge {
  font-size: 12px;
  color: #8c8c8c;
}

.text-block {
  padding: 12px 16px;
  background: #f6f8fb;
  border-radius: 8px;
}

.text-bubble {
  font-size: 14px;
  line-height: 1.7;
  color: #1f1f1f;
  white-space: pre-wrap;
  word-break: break-word;
}

.reasoning-collapse {
  margin-top: -4px;
}

.reasoning-body {
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.7;
  color: #8c8c8c;
  background: #fafafa;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}

.section-card {
  padding: 12px 14px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  background: #fff;
}

.vector-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.vector-sample {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
}

.sample-label {
  color: #595959;
}

.sample-code {
  padding: 4px 8px;
  font-size: 12px;
  background: #f5f5f5;
  border-radius: 4px;
}

.copy-btn {
  padding-inline: 4px;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.image-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.preview-img {
  width: 100%;
  max-height: 260px;
  object-fit: contain;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  background: #fafafa;
}

.media-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}

.media-player {
  width: 100%;
  max-height: 320px;
  border-radius: 6px;
  background: #111;
}

.url-line {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.url-text {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #1677ff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding-top: 10px;
  font-size: 12px;
  color: #8c8c8c;
  border-top: 1px dashed #f0f0f0;
}

.meta-icon {
  color: #faad14;
}

.meta-sep {
  margin: 0 2px;
}

.raw-btn {
  margin-left: auto;
}
</style>