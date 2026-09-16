<script setup lang="ts">
/**
 * 「去对话」前置弹窗：列出该工作流处于启用状态的 API Key，用户点击其中一个进入对话。
 *
 * 只做展示与选择，不在此处创建/启停 Key（那些操作仍归「API 访问」抽屉），
 * 避免同一份管理逻辑在两处重复维护。
 */
import type { ApiKeyListResp } from '#/api/ai-workflow';

import { computed } from 'vue';

import {
  CheckCircleOutlined,
  KeyOutlined,
  RightOutlined,
} from '@ant-design/icons-vue';
import { Empty, Tag } from 'ant-design-vue';

interface Props {
  /** 可选的启用状态 Key */
  keys?: ApiKeyListResp[];
  /** 是否打开 */
  open: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  keys: () => [],
});

const emit = defineEmits<{
  (e: 'update:open', open: boolean): void;
  (e: 'select', key: ApiKeyListResp): void;
}>();

/** 已过期的 Key 不再展示（不可用，选了也是 401） */
const usableKeys = computed(() =>
  (props.keys || []).filter(
    (item) =>
      item.status === 'ACTIVE' &&
      (!item.expireTime || new Date(item.expireTime).getTime() > Date.now()),
  ),
);

function handleClose() {
  emit('update:open', false);
}

function handleSelect(key: ApiKeyListResp) {
  emit('select', key);
  handleClose();
}

/** Key 明文展示时只留首尾，中间打点（完整值仍可在「API 访问」里查看） */
function maskKey(apiKey: string): string {
  if (!apiKey || apiKey.length <= 12) return apiKey || '-';
  return `${apiKey.slice(0, 6)}••••${apiKey.slice(-4)}`;
}

function formatTime(time?: null | string): string {
  return time ? String(time).replace('T', ' ').slice(0, 19) : '—';
}
</script>

<template>
  <a-modal
    :open="props.open"
    title="选择 API Key"
    :footer="null"
    :width="520"
    @update:open="handleClose"
  >
    <p class="modal-desc">
      该工作流已发布以下启用中的 API Key，选择一个开始对话。
    </p>

    <Empty
      v-if="usableKeys.length === 0"
      description="没有可用的 API Key，请先在「API 访问」中创建并启用"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
    />
    <div v-else class="key-list">
      <div
        v-for="key in usableKeys"
        :key="key.id"
        class="key-item"
        @click="handleSelect(key)"
      >
        <div class="key-icon"><KeyOutlined /></div>
        <div class="key-main">
          <div class="key-name">
            <span>{{ key.name }}</span>
            <Tag color="green"><CheckCircleOutlined /> 启用</Tag>
          </div>
          <div class="key-value">{{ maskKey(key.apiKey) }}</div>
          <div class="key-meta">
            <span>QPS {{ key.rateLimit || '不限' }}</span>
            <span>创建于 {{ formatTime(key.createTime) }}</span>
          </div>
        </div>
        <RightOutlined class="key-arrow" />
      </div>
    </div>
  </a-modal>
</template>

<style lang="less" scoped>
.modal-desc {
  margin: 0 0 12px;
  font-size: 13px;
  color: #8c8c8c;
}

.key-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 360px;
  padding-right: 4px;
  overflow-y: auto;
}

.key-item {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 12px 14px;
  cursor: pointer;
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  transition: all 0.2s;

  &:hover {
    border-color: #91caff;
    background: #f5fbff;
    box-shadow: 0 2px 8px rgb(24 144 255 / 10%);

    .key-arrow {
      color: #1890ff;
      transform: translateX(2px);
    }
  }

  .key-icon {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    font-size: 16px;
    color: #1890ff;
    background: rgb(24 144 255 / 8%);
    border-radius: 8px;
  }

  .key-main {
    flex: 1;
    min-width: 0;
  }

  .key-name {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 14px;
    font-weight: 500;
    color: #262626;
  }

  .key-value {
    margin-top: 2px;
    font-family: 'SFMono-Regular', Consolas, monospace;
    font-size: 12px;
    color: #8c8c8c;
  }

  .key-meta {
    display: flex;
    gap: 14px;
    margin-top: 4px;
    font-size: 12px;
    color: #bfbfbf;
  }

  .key-arrow {
    flex-shrink: 0;
    font-size: 12px;
    color: #bfbfbf;
    transition: all 0.2s;
  }
}
</style>
