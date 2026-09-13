<script setup lang="ts">
/**
 * 模型体验 - 模型选择器：展示当前用户已授权的模型（my-keys），按能力类型分组。
 * 卡片式单选：模型名 + 厂商徽标 + 类型标签，选中高亮。
 */
import type { MyKeyRep } from '../../api';

import { computed } from 'vue';

import { CopyOutlined, RobotOutlined } from '@ant-design/icons-vue';

const props = defineProps<{
  models: MyKeyRep[];
  selectedApplyId: number | null;
  loading?: boolean;
}>();

const emit = defineEmits<{
  'update:selectedApplyId': [id: number | null];
}>();

/** 按 category_label 分组（保持后端字典顺序） */
const grouped = computed(() => {
  const groups: { label: string; items: MyKeyRep[] }[] = [];
  const index = new Map<string, number>();
  for (const m of props.models) {
    const label = m.category_label || m.category || '未分类';
    const i = index.get(label);
    if (i === undefined) {
      index.set(label, groups.length);
      groups.push({ label, items: [m] });
    } else {
      groups[i]!.items.push(m);
    }
  }
  return groups;
});

const selectedModel = computed(
  () =>
    props.models.find((m) => m.apply_id === props.selectedApplyId) || null,
);

defineExpose({ selectedModel });
</script>

<template>
  <div class="model-picker">
    <div v-if="loading" class="picker-empty">加载中…</div>
    <template v-else-if="models.length === 0">
      <div class="picker-empty">
        <RobotOutlined class="empty-icon" />
        <p>暂无已授权模型</p>
        <p class="empty-tip">请先到「模型列表」申请模型并审批通过</p>
      </div>
    </template>
    <template v-else>
      <div v-for="group in grouped" :key="group.label" class="picker-group">
        <div class="group-title">{{ group.label }}</div>
        <div
          v-for="m in group.items"
          :key="m.apply_id"
          class="model-card"
          :class="{
            active: m.apply_id === selectedApplyId,
          }"
          @click="emit('update:selectedApplyId', m.apply_id)"
        >
          <div class="card-head">
            <span class="model-name">{{ m.model_name }}</span>
            <span class="provider-tag">{{ m.provider_label || m.provider }}</span>
          </div>
          <div class="card-meta">
            <span class="category-tag">{{ m.category_label || m.category }}</span>
            <CopyOutlined v-if="m.apply_id === selectedApplyId" class="check-icon" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.model-picker {
  height: 100%;
  overflow-y: auto;
  padding: 12px;
}

.picker-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #8c8c8c;
  text-align: center;
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.empty-tip {
  font-size: 12px;
  color: #bfbfbf;
}

.picker-group {
  margin-bottom: 14px;
}

.group-title {
  padding: 4px 4px 8px;
  font-size: 12px;
  font-weight: 600;
  color: #8c8c8c;
  letter-spacing: 1px;
}

.model-card {
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}

.model-card:hover {
  border-color: #1677ff;
  box-shadow: 0 2px 8px rgb(22 119 255 / 12%);
}

.model-card.active {
  border-color: #1677ff;
  background: linear-gradient(135deg, #e6f4ff 0%, #f0f7ff 100%);
  box-shadow: 0 2px 8px rgb(22 119 255 / 16%);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.model-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f1f1f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.provider-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 11px;
  border-radius: 4px;
  background: #f0f0f0;
  color: #595959;
}

.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

.category-tag {
  font-size: 11px;
  color: #1677ff;
  background: #e6f4ff;
  padding: 1px 6px;
  border-radius: 4px;
}

.check-icon {
  color: #1677ff;
  font-size: 12px;
}
</style>