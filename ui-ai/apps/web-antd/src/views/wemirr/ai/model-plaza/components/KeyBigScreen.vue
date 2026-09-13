<script setup lang="ts">
/**
 * 我的 Key 大屏查看：全屏固定层，大字号展示模型接口信息与常用参数。
 * ESC 或右上角关闭按钮退出（emit update:open false）。
 */
import type { MyKeyRep } from '../api';

import { computed, onMounted, onUnmounted } from 'vue';

import { CloseOutlined } from '@ant-design/icons-vue';

defineOptions({ name: 'KeyBigScreen' });

const props = defineProps<{
  keyData: MyKeyRep | null;
  open: boolean;
}>();

const emit = defineEmits<{ 'update:open': [open: boolean] }>();

const gatewayDisplay = computed(
  () => props.keyData?.gateway_url || '（未配置网关地址）',
);
const rateDisplay = computed(() =>
  props.keyData?.rate_limit_qps
    ? `并发 ${props.keyData.rate_limit_qps}`
    : '不限制',
);
const params = computed(() => props.keyData?.common_params || []);

function close() {
  emit('update:open', false);
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close();
}

onMounted(() => window.addEventListener('keydown', onKeydown));
onUnmounted(() => window.removeEventListener('keydown', onKeydown));
</script>

<template>
  <div v-if="open && keyData" class="big-screen">
    <button class="bs-close" @click="close">
      <CloseOutlined />
      <span>退出</span>
    </button>

    <div class="bs-inner">
      <div class="bs-title">{{ keyData.name }}</div>
      <div class="bs-sub">
        {{ keyData.category_label }} · {{ keyData.provider_label }} ·
        {{ keyData.model_name }}
      </div>

      <div class="bs-grid">
        <div class="bs-block">
          <div class="bs-label">网关地址</div>
          <div class="bs-value bs-mono">{{ gatewayDisplay }}</div>
        </div>
        <div class="bs-block">
          <div class="bs-label">API Key</div>
          <div class="bs-value bs-mono bs-key">{{ keyData.api_key }}</div>
        </div>
        <div class="bs-block">
          <div class="bs-label">限流</div>
          <div class="bs-value">{{ rateDisplay }}</div>
        </div>
        <div class="bs-block">
          <div class="bs-label">流式 / 深度思考</div>
          <div class="bs-value">
            {{ keyData.supports_stream ? '支持流式' : '不支持流式' }} ·
            {{ keyData.supports_thinking ? '支持深度思考' : '不支持深度思考' }}
          </div>
        </div>
        <div class="bs-block">
          <div class="bs-label">开启流式参数</div>
          <div class="bs-value bs-mono">{{ keyData.stream_param || 'stream' }}</div>
        </div>
        <div class="bs-block">
          <div class="bs-label">开启思考参数</div>
          <div class="bs-value bs-mono">{{ keyData.thinking_param || '-' }}</div>
        </div>
      </div>

      <div v-if="params.length" class="bs-params">
        <div class="bs-label">常用参数</div>
        <div class="bs-param-table">
          <div class="bs-param-head">
            <span>参数名</span>
            <span>默认值</span>
            <span>说明</span>
            <span>类型</span>
          </div>
          <div v-for="p in params" :key="p.name" class="bs-param-row">
            <span class="bs-mono">{{ p.name }}</span>
            <span class="bs-mono">{{
              p.default === undefined || p.default === null || p.default === ''
                ? '-'
                : String(p.default)
            }}</span>
            <span>{{ p.desc || '-' }}</span>
            <span>{{ p.type }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.big-screen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  overflow: auto;
  padding: 48px 64px;
  color: #e6f4ff;
  background: radial-gradient(circle at 30% 20%, #0b2a4a 0%, #04101f 60%, #000 100%);
}

.bs-close {
  position: fixed;
  top: 24px;
  right: 32px;
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 8px 16px;
  font-size: 16px;
  color: #e6f4ff;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 8px;
}

.bs-close:hover {
  background: rgba(255, 255, 255, 0.16);
}

.bs-inner {
  max-width: 1280px;
  margin: 0 auto;
}

.bs-title {
  font-size: 40px;
  font-weight: 700;
  line-height: 1.3;
}

.bs-sub {
  margin-top: 8px;
  margin-bottom: 32px;
  font-size: 20px;
  color: #69c0ff;
}

.bs-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 24px 48px;
}

.bs-block {
  padding: 20px 24px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
}

.bs-label {
  margin-bottom: 10px;
  font-size: 16px;
  color: #8c8c8c;
}

.bs-value {
  font-size: 26px;
  font-weight: 600;
  word-break: break-all;
}

.bs-mono {
  font-family: 'Consolas', 'Monaco', monospace;
}

.bs-key {
  color: #ffd666;
}

.bs-params {
  margin-top: 36px;
}

.bs-param-table {
  margin-top: 10px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
}

.bs-param-head,
.bs-param-row {
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 2fr 0.8fr;
  gap: 16px;
  padding: 14px 24px;
  font-size: 20px;
}

.bs-param-head {
  color: #8c8c8c;
  background: rgba(255, 255, 255, 0.06);
}

.bs-param-row {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

@media (max-width: 900px) {
  .bs-grid {
    grid-template-columns: 1fr;
  }
}
</style>
