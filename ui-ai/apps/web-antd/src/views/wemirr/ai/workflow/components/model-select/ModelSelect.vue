<script setup lang="ts">
/**
 * ModelSelect - 工作流节点通用模型选择器（对齐 common_model 设计）
 * - 模型类型选择（可选）：切换类型后按类型拉取当前用户审批通过的模型列表
 * - 模型下拉：展示模型名称；空列表时提示前往模型广场申请
 * 数据源：GET /models/list?type=xxx（type 支持 12 能力类型 code 或分组值，
 *         仅返回当前用户审批通过的模型）
 *
 * 【设计对齐 2026-09】「直连/非直连 + 接口后缀」概念已整体废弃，
 * 选择器只负责选「类型 + 模型」，不再维护后缀 / 直连状态。
 */
import { computed, onMounted, ref, watch } from 'vue';

import { useRouter } from 'vue-router';

import { MODEL_TYPE_TEXT } from '#/api/ai-workflow/const';
import { listAiModels } from '#/api/ai-workflow';
import type { AiModelOption } from '#/api/ai-workflow/types';

// ==================== Props ====================

interface Props {
  /** 已选模型 ID (v-model) */
  modelValue?: number;
  /** 已选模型类型 (v-model)；typeOptions 为空时不展示类型选择 */
  modelType?: string;
  /** 模型类型选项；为空表示固定类型（如 text_rerank），按 defaultType 直接加载 */
  typeOptions?: { label: string; value: string }[];
  /** 未展示类型选择时的固定类型（默认 text 分组） */
  defaultType?: string;
  /** 模型下拉占位符 */
  placeholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: undefined,
  modelType: '',
  typeOptions: () => [],
  defaultType: MODEL_TYPE_TEXT,
  placeholder: '选择模型',
});

// ==================== Emits ====================

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | undefined): void;
  (e: 'update:modelType', value: string): void;
  (e: 'change'): void;
}>();

// ==================== 状态 ====================

const router = useRouter();

const models = ref<AiModelOption[]>([]);
const loadingModels = ref(false);

/** 当前生效的模型类型（显式 modelType 或固定 defaultType） */
const currentType = computed(() => props.modelType || props.defaultType);

const emptyHint = computed(
  () => !loadingModels.value && models.value.length === 0,
);

// ==================== 数据加载 ====================

async function loadModels(type: string) {
  loadingModels.value = true;
  try {
    models.value = await listAiModels(type);
  } catch {
    models.value = [];
  } finally {
    loadingModels.value = false;
  }
}

// ==================== 交互 ====================

function handleTypeChange(value: string) {
  // 类型切换：清空已选模型，重新拉列表
  emit('update:modelType', value);
  emit('update:modelValue', undefined);
  loadModels(value || props.defaultType);
  emit('change');
}

function handleModelChange(value: number) {
  emit('update:modelValue', value);
  emit('change');
}

function goApply() {
  router.push('/model-plaza');
}

// ==================== 监听 ====================

watch(
  () => props.modelType,
  (value) => {
    loadModels(value || props.defaultType);
  },
);

onMounted(() => {
  loadModels(currentType.value);
});
</script>

<template>
  <div class="model-select">
    <!-- 模型类型（可选） -->
    <a-form-item v-if="typeOptions.length > 0" label="模型类型" required>
      <a-select
        :value="modelType || defaultType"
        placeholder="选择模型类型"
        @change="handleTypeChange"
      >
        <a-select-option
          v-for="opt in typeOptions"
          :key="opt.value"
          :value="opt.value"
        >
          {{ opt.label }}
        </a-select-option>
      </a-select>
    </a-form-item>

    <!-- 模型 -->
    <a-form-item label="模型" required>
      <a-select
        :value="modelValue"
        :placeholder="placeholder"
        :loading="loadingModels"
        :not-found-content="null"
        @change="handleModelChange"
      >
        <a-select-option
          v-for="model in models"
          :key="model.id"
          :value="model.id"
        >
          <span class="model-name">
            {{ model.name }}
            <a-tag v-if="model.provider" class="model-tag" color="blue">
              {{ model.provider }}
            </a-tag>
          </span>
        </a-select-option>
      </a-select>
      <!-- 空态：无审批通过模型 -->
      <div v-if="emptyHint" class="model-empty-hint">
        <span>当前类型下没有已审批通过的模型。</span>
        <a-button type="link" size="small" @click="goApply">
          前往模型广场申请使用 →
        </a-button>
      </div>
    </a-form-item>
  </div>
</template>

<style scoped>
.model-select :deep(.model-name) {
  vertical-align: middle;
}

.model-tag {
  margin-inline-start: 4px;
  line-height: 16px;
  font-size: 12px;
}

.model-empty-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  color: #ad6800;
  font-size: 12px;
  line-height: 20px;
}
</style>
