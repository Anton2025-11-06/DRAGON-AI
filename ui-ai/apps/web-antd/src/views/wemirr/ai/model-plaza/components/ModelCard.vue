<script setup lang="ts">
/**
 * 模型卡片：maxkb 模型卡片设计的 antd 适配版
 * 展示模型基本信息 + 申请状态；操作按钮由父页面统一分发（RBAC 控制）
 */
import { computed } from 'vue';

import type * as api from '../api';

const props = defineProps<{
  model: api.ModelPageRep;
  /** 是否拥有管理权限（增删改） */
  canEdit: boolean;
  canDelete: boolean;
}>();

const emit = defineEmits<{
  apply: [model: api.ModelPageRep];
  tutorial: [model: api.ModelPageRep];
  myKey: [model: api.ModelPageRep];
  edit: [model: api.ModelPageRep];
  toggle: [model: api.ModelPageRep];
  delete: [model: api.ModelPageRep];
}>();

/** 分类 emoji 图标（maxkb 卡片图标位） */
const categoryEmoji: Record<string, string> = {
  TEXT_GEN: '✍️',
  EMBEDDING: '📐',
  RERANK: '🔀',
  MULTIMODAL: '🧠',
  IMAGE_GEN: '🎨',
  AUDIO_GEN: '🎙️',
  VIDEO_GEN: '🎬',
};

/** 申请状态展示 */
const applyStatusMeta = computed<{ label: string; color: string } | null>(
  () => {
    const s = props.model.apply_status;
    if (s === null || s === undefined) return null;
    return {
      0: { label: '待审批', color: 'processing' },
      1: { label: '已通过', color: 'success' },
      2: { label: '已拒绝', color: 'error' },
    }[s] as any;
  },
);

/** 是否可申请/重新申请：非管理端、未申请或已拒绝、模型启用中（待审批/已通过不可重复申请） */
const canApply = computed(
  () =>
    // !props.canEdit &&
    props.model.apply_status !== 0 &&
    props.model.apply_status !== 1 &&
    props.model.status !== false,
);
</script>

<template>
  <a-card class="model-card" size="small" hoverable :bordered="true">
    <!-- 头部：图标 + 名称 + 状态标签 -->
    <div class="model-card__header">
      <span class="model-card__icon">{{
        categoryEmoji[model.category] || '🤖'
      }}</span>
      <a-tooltip :title="model.name">
        <span class="model-card__name">{{ model.name }}</span>
      </a-tooltip>
      <span class="model-card__tags">
        <a-tag v-if="model.status" color="success">启用</a-tag>
        <a-tag v-else color="error">停用</a-tag>
      </span>
    </div>
    <div v-if="applyStatusMeta" class="model-card__apply-tag">
      <a-tag :color="applyStatusMeta.color">{{ applyStatusMeta.label }}</a-tag>
    </div>

    <!-- 信息区：分类 / 提供商 / 模型标识 / 限流 -->
    <div class="model-card__body">
      <div class="model-card__row">
        <span class="model-card__label">分类</span>
        <span class="model-card__value">{{ model.category_label }}</span>
        <span class="model-card__label model-card__label--margin">提供商</span>
        <span class="model-card__value">{{ model.provider_label }}</span>
      </div>
      <div class="model-card__row">
        <span class="model-card__label">模型标识</span>
        <a-tooltip :title="model.model_name">
          <span class="model-card__value model-card__value--ellipsis">{{
            model.model_name
          }}</span>
        </a-tooltip>
      </div>
      <div class="model-card__row">
        <span class="model-card__label">限流</span>
        <span class="model-card__value">
          并发 {{ model.rate_limit_qps || '不限' }}
        </span>
      </div>
    </div>

    <!-- 操作区 -->
    <div class="model-card__footer">
      <a-space wrap :size="4">
        <a-button
          v-if="canApply"
          type="primary"
          size="small"
          @click="emit('apply', model)"
        >
          {{ model.apply_status === 2 ? '重新申请' : '申请使用' }}
        </a-button>
        <a-button size="small" @click="emit('tutorial', model)"
          >查看教程</a-button
        >
        <a-button
          v-if="model.apply_status === 1"
          size="small"
          @click="emit('myKey', model)"
        >
          我的Key
        </a-button>
        <template v-if="canEdit">
          <a-button size="small" @click="emit('edit', model)">编辑</a-button>
          <a-button size="small" @click="emit('toggle', model)">
            {{ model.status ? '停用' : '启用' }}
          </a-button>
        </template>
        <a-button
          v-if="canDelete"
          size="small"
          danger
          @click="emit('delete', model)"
        >
          删除
        </a-button>
      </a-space>
    </div>
  </a-card>
</template>

<style scoped>
.model-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.model-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.model-card__icon {
  font-size: 20px;
  flex-shrink: 0;
}

.model-card__name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.model-card__tags {
  flex-shrink: 0;
}

.model-card__apply-tag {
  margin-top: 4px;
}

.model-card__body {
  margin-top: 10px;
  flex: 1;
}

.model-card__row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 22px;
  min-width: 0;
}

.model-card__label {
  color: rgba(100, 106, 115, 1);
  flex-shrink: 0;
}

.model-card__label--margin {
  margin-left: 12px;
}

.model-card__value {
  color: rgba(29, 35, 41, 1);
}

.model-card__value--ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.model-card__footer {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(5, 5, 5, 0.06);
}
</style>
