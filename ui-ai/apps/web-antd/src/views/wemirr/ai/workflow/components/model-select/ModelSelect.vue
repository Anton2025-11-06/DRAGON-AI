<script setup lang="ts">
/**
 * ModelSelect - 工作流节点通用模型选择器
 * - 模型类型选择（可选）：切换类型后按类型拉取当前用户审批通过的模型列表
 * - 模型下拉：展示直连/非直连标识；空列表时提示前往模型广场申请
 * - 接口后缀选择（非直连模型）：展示后缀说明 desc
 * 数据源：GET /models/list?type=xxx（仅返回当前用户审批通过的模型）
 */
import { computed, onMounted, ref, watch } from 'vue';

import { useRouter } from 'vue-router';

import { MODEL_TYPE_TEXT } from '#/api/ai-workflow/const';
import { listAiModels } from '#/api/ai-workflow';
import type {
  AiModelOption,
  ModelSuffixOption,
} from '#/api/ai-workflow/types';

// ==================== Props ====================

interface Props {
  /** 已选模型 ID (v-model) */
  modelValue?: number;
  /** 已选模型类型 (v-model)；typeOptions 为空时不展示类型选择 */
  modelType?: string;
  /** 已选接口后缀 (v-model, suffixValue) */
  suffixValue?: string;
  /** 已选模型是否直连 (v-model)：0=非直连 1=直连；供画布校验“非直连必须选后缀” */
  modelIsDirect?: number;
  /** 模型类型选项；为空表示固定类型（如 rerank），按 defaultType 直接加载 */
  typeOptions?: { label: string; value: string }[];
  /** 未展示类型选择时的固定类型（默认 text） */
  defaultType?: string;
  /** 是否展示接口后缀选择（仅非直连模型，默认 true） */
  showSuffix?: boolean;
  /** 模型下拉占位符 */
  placeholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: undefined,
  modelType: '',
  suffixValue: '',
  modelIsDirect: undefined,
  typeOptions: () => [],
  defaultType: MODEL_TYPE_TEXT,
  showSuffix: true,
  placeholder: '选择模型',
});

// ==================== Emits ====================

const emit = defineEmits<{
  (e: 'update:modelValue', value: number | undefined): void;
  (e: 'update:modelType', value: string): void;
  (e: 'update:suffixValue', value: string | undefined): void;
  (e: 'update:modelIsDirect', value: number | undefined): void;
  (e: 'change'): void;
}>();

// ==================== 状态 ====================

const router = useRouter();

const models = ref<AiModelOption[]>([]);
const loadingModels = ref(false);

/** 当前生效的模型类型（显式 modelType 或固定 defaultType） */
const currentType = computed(
  () => props.modelType || props.defaultType,
);

/** 已选模型对象 */
const selectedModel = computed(
  () => models.value.find((m) => m.id === props.modelValue) || null,
);

/** 已选模型的后缀列表 */
const suffixes = computed<ModelSuffixOption[]>(
  () => selectedModel.value?.suffixes || [],
);

/** 非直连模型未选后缀（必选校验不通过） */
const suffixMissing = computed(
  () => selectedModel.value?.isDirect === 0 && !props.suffixValue,
);

/** 非直连模型未选后缀时的校验提示（有后缀可选 vs 模型未配置后缀） */
const suffixHelp = computed(() => {
  if (!suffixMissing.value) {
    return '';
  }
  return suffixes.value.length > 0
    ? '非直连模型必须选择接口后缀'
    : '该模型未配置接口后缀，无法使用（请到模型广场为该模型补充后缀后重新选择）';
});

/** 已选后缀的说明文本 */
const suffixDesc = computed(() => {
  const hit = suffixes.value.find((s) => s.url === props.suffixValue);
  return hit ? hit.desc || '' : '';
});

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
  // 类型切换：清空已选模型与后缀，重新拉列表
  emit('update:modelType', value);
  emit('update:modelValue', undefined);
  emit('update:suffixValue', undefined);
  emit('update:modelIsDirect', undefined);
  loadModels(value || props.defaultType);
  emit('change');
}

function normalizeSuffix() {
  // 模型切换后旧后缀可能失效：不在新模型后缀列表内则清空
  if (!props.suffixValue) {
    return;
  }
  const hit = suffixes.value.some((s) => s.url === props.suffixValue);
  if (!hit) {
    emit('update:suffixValue', undefined);
  }
}

function handleModelChange(value: number) {
  normalizeSuffix();
  // 同步所选模型直连状态（画布校验“非直连必须选后缀”依赖）
  const model = models.value.find((m) => m.id === value);
  emit('update:modelIsDirect', model?.isDirect);
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
    <a-form-item
      v-if="typeOptions.length > 0"
      label="模型类型"
      required
    >
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
        placeholder="选择模型"
        :loading="loadingModels"
        :not-found-content="null"
        @change="
          (value: number) => {
            emit('update:modelValue', value);
            handleModelChange(value);
          }
        "
      >
        <a-select-option
          v-for="model in models"
          :key="model.id"
          :value="model.id"
        >
          <span class="model-name">
            {{ model.name }}
            <a-tag
              v-if="model.isDirect === 1"
              class="model-tag"
              color="green"
            >
              直连
            </a-tag>
            <a-tag v-else class="model-tag" color="orange">非直连</a-tag>
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

    <!-- 接口后缀（仅非直连模型，必选） -->
    <a-form-item
      v-if="showSuffix && selectedModel && selectedModel.isDirect === 0"
      label="接口后缀"
      required
      :validate-status="suffixMissing ? 'error' : ''"
      :help="suffixMissing ? suffixHelp : undefined"
    >
      <a-select
        :value="suffixValue"
        placeholder="请选择接口后缀"
        @change="
          (value: string) => {
            emit('update:suffixValue', value);
            emit('change');
          }
        "
      >
        <a-select-option
          v-for="(s, index) in suffixes"
          :key="index"
          :value="s.url"
        >
          {{ s.desc || s.url }}
        </a-select-option>
      </a-select>
      <div v-if="suffixDesc" class="model-suffix-desc">
        {{ suffixDesc }}
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

.model-suffix-desc {
  margin-top: 4px;
  color: #8c8c8c;
  font-size: 12px;
  line-height: 18px;
  word-break: break-all;
}
</style>