<script setup lang="ts">
/** 模型卡片：展示模型信息、申请状态和当前权限允许的操作。 */
import { computed } from 'vue';

import { DeploymentUnitOutlined } from '@ant-design/icons-vue';

import type * as api from '../api';

const props = defineProps<{
  model: api.ModelPageRep;
  canEdit: boolean;
  canDelete: boolean;
}>();

const emit = defineEmits<{
  apply: [model: api.ModelPageRep];
  myKey: [model: api.ModelPageRep];
  edit: [model: api.ModelPageRep];
  toggle: [model: api.ModelPageRep];
  delete: [model: api.ModelPageRep];
}>();

const applyStatusMeta = computed<{ label: string; color: string } | null>(() => {
  const s = props.model.apply_status;
  if (s === null || s === undefined) return null;
  return {
    0: { label: '待审批', color: 'processing' },
    1: { label: '已通过', color: 'success' },
    2: { label: '已拒绝', color: 'error' },
  }[s] as any;
});

const canApply = computed(
  () =>
    props.model.apply_status !== 0 &&
    props.model.apply_status !== 1 &&
    props.model.status !== false,
);
</script>

<template>
  <a-card class="model-card" size="small" :bordered="true">
    <div class="model-card__header">
      <span class="model-card__mark">
        <DeploymentUnitOutlined />
      </span>
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

    <div class="model-card__body">
      <div class="model-card__row">
        <span class="model-card__label">分类</span>
        <span class="model-card__value">{{ model.category_label }}</span>
        <span class="model-card__label model-card__label--margin">提供商</span>
        <span class="model-card__value model-card__value--ellipsis">
          {{ model.provider_label }}
        </span>
      </div>
      <div class="model-card__row">
        <span class="model-card__label">模型标识</span>
        <a-tooltip :title="model.model_name">
          <span class="model-card__value model-card__value--ellipsis">
            {{ model.model_name }}
          </span>
        </a-tooltip>
      </div>
      <div class="model-card__row">
        <span class="model-card__label">限流</span>
        <span class="model-card__value">
          并发 {{ model.rate_limit_qps || '不限' }}
        </span>
      </div>
      <div class="model-card__row">
        <span class="model-card__label">能力</span>
        <a-tooltip
          :title="`开启流式参数：${model.stream_param || 'stream'}；开启思考参数：${model.thinking_param || '-'}`"
        >
          <span class="model-card__value model-card__value--ellipsis">
            流式 {{ model.supports_stream ? '支持' : '不支持' }} · 深度思考
            {{ model.supports_thinking ? '支持' : '不支持' }} · 常用参数
            {{ (model.common_params || []).length }} 个
          </span>
        </a-tooltip>
      </div>
    </div>

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
        <a-button
          v-if="model.apply_status === 1"
          size="small"
          @click="emit('myKey', model)"
        >
          我的 Key
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
  display: flex;
  flex-direction: column;
  height: 100%;
  border-color: var(--border-color, rgb(15 23 42 / 9%));
  border-radius: 12px;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.model-card:hover {
  border-color: hsl(var(--primary) / 35%);
  box-shadow: 0 8px 24px rgb(15 23 42 / 8%);
  transform: translateY(-2px);
}

.model-card__header {
  display: flex;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.model-card__mark {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: 18px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 9%);
  border-radius: 10px;
}

.model-card__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-color-1, #1f2329);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-card__tags {
  flex-shrink: 0;
}

.model-card__apply-tag {
  margin-top: 6px;
}

.model-card__body {
  flex: 1;
  padding: 10px 0;
  margin-top: 10px;
  border-top: 1px solid var(--border-color, #edf0f4);
}

.model-card__row {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  font-size: 12px;
  line-height: 24px;
}

.model-card__label {
  flex-shrink: 0;
  color: var(--text-color-3, #8a9099);
}

.model-card__label--margin {
  margin-left: 8px;
}

.model-card__value {
  min-width: 0;
  color: var(--text-color-2, #4e5969);
}

.model-card__value--ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-card__footer {
  padding-top: 10px;
  margin-top: 2px;
  border-top: 1px solid var(--border-color, #edf0f4);
}
</style>
