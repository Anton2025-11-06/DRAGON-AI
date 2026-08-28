<script setup lang="ts">
/**
 * HTTP 节点配置表单
 * 配置 HTTP 请求参数
 */
import type { HttpRequestNodeConfig } from '#/api/ai-workflow/types';

import { reactive, ref, watch } from 'vue';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: HttpRequestNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: HttpRequestNodeConfig): void;
}>();

// 请求头列表
interface HeaderItem {
  key: string;
  value: string;
}

const headers = ref<HeaderItem[]>([]);
const bodyText = ref('');

// 表单数据
const formData = reactive<HttpRequestNodeConfig>({
  url: '',
  method: 'GET',
  headers: {},
  body: undefined,
  connectTimeout: 30_000,
  readTimeout: 30_000,
  outputVariable: '',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      url: config.url || '',
      method: config.method || 'GET',
      headers: config.headers || {},
      body: config.body,
      connectTimeout: config.connectTimeout ?? 30_000,
      readTimeout: config.readTimeout ?? 30_000,
      outputVariable: config.outputVariable || '',
    });

    // 转换 headers 对象为数组
    headers.value = Object.entries(config.headers || {}).map(
      ([key, value]) => ({
        key,
        value,
      }),
    );

    // 转换 body 为字符串
    if (config.body) {
      bodyText.value =
        typeof config.body === 'string'
          ? config.body
          : JSON.stringify(config.body, null, 2);
    } else {
      bodyText.value = '';
    }
  },
  { immediate: true, deep: true },
);

// 添加请求头
function addHeader() {
  headers.value.push({ key: '', value: '' });
}

// 移除请求头
function removeHeader(index: number) {
  headers.value.splice(index, 1);
  handleHeadersChange();
}

// 处理请求头变更
function handleHeadersChange() {
  const headersObj: Record<string, string> = {};
  headers.value.forEach((h) => {
    if (h.key) {
      headersObj[h.key] = h.value;
    }
  });
  formData.headers = headersObj;
  handleChange();
}

// 处理请求体变更
function handleBodyChange() {
  try {
    formData.body = bodyText.value ? JSON.parse(bodyText.value) : undefined;
  } catch {
    // 如果不是有效 JSON，保存为字符串
    formData.body = bodyText.value || undefined;
  }
  handleChange();
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="请求方法" required>
      <a-select v-model:value="formData.method" @change="handleChange">
        <a-select-option value="GET">GET</a-select-option>
        <a-select-option value="POST">POST</a-select-option>
        <a-select-option value="PUT">PUT</a-select-option>
        <a-select-option value="DELETE">DELETE</a-select-option>
        <a-select-option value="PATCH">PATCH</a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item label="请求 URL" required>
      <VariableInput
        v-model="formData.url"
        :current-node-id="nodeId"
        placeholder="https://api.example.com/endpoint"
        @change="handleChange"
      />
      <div class="form-hint">
        支持使用 <code v-pre>{{ 变量名 }}</code> 引用变量
      </div>
    </a-form-item>

    <a-form-item label="请求头">
      <div class="headers-container">
        <div
          v-for="(header, index) in headers"
          :key="index"
          class="header-item"
        >
          <a-input
            v-model:value="header.key"
            placeholder="Header 名称"
            class="header-key"
            @change="handleHeadersChange"
          />
          <VariableInput
            v-model="header.value"
            :current-node-id="nodeId"
            placeholder="Header 值"
            class="header-value"
            @change="handleHeadersChange"
          />
          <a-button
            type="text"
            size="small"
            danger
            @click="removeHeader(index)"
          >
            <template #icon>
              <DeleteOutlined />
            </template>
          </a-button>
        </div>
        <a-button type="dashed" size="small" @click="addHeader">
          <template #icon>
            <PlusOutlined />
          </template>
          添加请求头
        </a-button>
      </div>
    </a-form-item>

    <a-form-item
      v-if="['POST', 'PUT', 'PATCH'].includes(formData.method || '')"
      label="请求体"
    >
      <a-textarea
        v-model:value="bodyText"
        :rows="6"
        placeholder="{&quot;key&quot;: &quot;value&quot;}"
        @change="handleBodyChange"
      />
      <div class="form-hint">
        JSON 格式，支持使用 <code v-pre>{{ 变量名 }}</code> 引用变量
      </div>
    </a-form-item>

    <a-form-item label="连接超时 (毫秒)">
      <a-input-number
        v-model:value="formData.connectTimeout"
        :min="1000"
        :max="300000"
        :step="1000"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="读取超时 (毫秒)">
      <a-input-number
        v-model:value="formData.readTimeout"
        :min="1000"
        :max="300000"
        :step="1000"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: http_response"
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

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .headers-container {
    display: flex;
    flex-direction: column;
    gap: 8px;

    .header-item {
      display: flex;
      gap: 8px;
      align-items: center;

      .header-key {
        flex: 1;
      }

      .header-value {
        flex: 2;
      }
    }
  }
}
</style>
