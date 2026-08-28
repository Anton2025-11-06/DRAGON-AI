<script setup lang="ts">
/**
 * Knowledge 节点配置表单
 * 配置知识库检索参数
 * 支持元数据过滤和重排序配置
 */
import type {
  AiModelOption,
  FilterOperator,
  KnowledgeBaseOption,
  KnowledgeRetrievalConfig,
  MetadataFilter,
  RerankConfig,
  RetrievalMode,
} from '#/api/ai-workflow/types';

import { onMounted, reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';

import { listAiModels, listKnowledgeBases } from '#/api/ai-workflow';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: KnowledgeRetrievalConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: KnowledgeRetrievalConfig): void;
}>();

const knowledgeBases = ref<KnowledgeBaseOption[]>([]);
const loadingKnowledgeBases = ref(false);
const rerankModels = ref<AiModelOption[]>([]);
const loadingModels = ref(false);

// 默认重排序配置
const defaultRerankConfig: RerankConfig = {
  enabled: false,
  rerankModelId: undefined,
  topN: 3,
};

// 表单数据
const formData = reactive<
  KnowledgeRetrievalConfig & { rerankConfig: RerankConfig }
>({
  knowledgeBaseIds: [],
  queryVariable: '',
  topK: 5,
  scoreThreshold: 0.5,
  retrievalMode: 'VECTOR' as RetrievalMode,
  rerankConfig: { ...defaultRerankConfig },
  metadataFilters: [],
  outputVariable: '',
  includeMetadata: false,
  includeScore: false,
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      knowledgeBaseIds: config.knowledgeBaseIds || [],
      queryVariable: config.queryVariable || '',
      topK: config.topK ?? 5,
      scoreThreshold: config.scoreThreshold ?? 0.5,
      retrievalMode: config.retrievalMode || 'VECTOR',
      rerankConfig: config.rerankConfig
        ? { ...defaultRerankConfig, ...config.rerankConfig }
        : { ...defaultRerankConfig },
      metadataFilters: config.metadataFilters || [],
      outputVariable: config.outputVariable || '',
      includeMetadata: config.includeMetadata ?? false,
      includeScore: config.includeScore ?? false,
    });
  },
  { immediate: true, deep: true },
);

// 加载知识库列表
async function loadKnowledgeBases() {
  loadingKnowledgeBases.value = true;
  try {
    knowledgeBases.value = await listKnowledgeBases();
  } catch {
    knowledgeBases.value = [];
  } finally {
    loadingKnowledgeBases.value = false;
  }
}

// 加载重排序模型列表
async function loadRerankModels() {
  loadingModels.value = true;
  try {
    rerankModels.value = await listAiModels('RERANK');
  } catch {
    rerankModels.value = [];
  } finally {
    loadingModels.value = false;
  }
}

// 添加过滤条件
function addFilter() {
  formData.metadataFilters = formData.metadataFilters || [];
  formData.metadataFilters.push({
    field: '',
    operator: 'EQUALS' as FilterOperator,
    value: '',
  });
  handleChange();
}

// 移除过滤条件
function removeFilter(index: number) {
  formData.metadataFilters?.splice(index, 1);
  handleChange();
}

// 处理配置变更
function handleChange() {
  const config: KnowledgeRetrievalConfig = {
    knowledgeBaseIds: formData.knowledgeBaseIds,
    queryVariable: formData.queryVariable,
    topK: formData.topK,
    scoreThreshold: formData.scoreThreshold,
    retrievalMode: formData.retrievalMode,
    rerankConfig: formData.rerankConfig.enabled
      ? { ...formData.rerankConfig }
      : undefined,
    metadataFilters:
      formData.metadataFilters && formData.metadataFilters.length > 0
        ? formData.metadataFilters.filter(
            (f: MetadataFilter) => f.field && f.operator,
          )
        : undefined,
    outputVariable: formData.outputVariable,
    includeMetadata: formData.includeMetadata,
    includeScore: formData.includeScore,
  };
  emit('update:config', config);
}

onMounted(() => {
  loadKnowledgeBases();
  loadRerankModels();
});
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="知识库" required>
      <a-select
        v-model:value="formData.knowledgeBaseIds"
        mode="multiple"
        placeholder="选择知识库（可多选）"
        :loading="loadingKnowledgeBases"
        @change="handleChange"
      >
        <a-select-option
          v-for="kb in knowledgeBases"
          :key="kb.id"
          :value="kb.id"
        >
          {{ kb.name }}
        </a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item label="查询变量">
      <VariableInput
        v-model="formData.queryVariable"
        :current-node-id="nodeId"
        placeholder="选择要检索的文本变量"
        :filter-types="['string']"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="检索数量 (Top K)">
      <a-input-number
        v-model:value="formData.topK"
        :min="1"
        :max="20"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="form-hint">返回最相关的文档片段数量</div>
    </a-form-item>

    <a-form-item label="相似度阈值">
      <a-row :gutter="12">
        <a-col :span="18">
          <a-slider
            v-model:value="formData.scoreThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            @change="handleChange"
          />
        </a-col>
        <a-col :span="6">
          <a-input-number
            v-model:value="formData.scoreThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            size="small"
            style="width: 100%"
            @change="handleChange"
          />
        </a-col>
      </a-row>
      <div class="form-hint">低于此阈值的结果将被过滤</div>
    </a-form-item>

    <a-form-item label="检索模式">
      <a-select v-model:value="formData.retrievalMode" @change="handleChange">
        <a-select-option value="VECTOR">向量检索</a-select-option>
        <a-select-option value="FULLTEXT">全文检索</a-select-option>
        <a-select-option value="HYBRID">混合检索</a-select-option>
      </a-select>
    </a-form-item>

    <!-- 重排序配置 -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      重排序配置
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          启用重排序
          <a-tooltip title="使用重排序模型对检索结果进行二次排序，提高相关性">
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-switch
        v-model:checked="formData.rerankConfig.enabled"
        @change="handleChange"
      />
    </a-form-item>

    <template v-if="formData.rerankConfig.enabled">
      <a-form-item label="重排序模型">
        <a-select
          v-model:value="formData.rerankConfig.rerankModelId"
          placeholder="选择重排序模型"
          :loading="loadingModels"
          @change="handleChange"
        >
          <a-select-option
            v-for="model in rerankModels"
            :key="model.id"
            :value="model.id"
          >
            {{ model.name }}
          </a-select-option>
        </a-select>
      </a-form-item>

      <a-form-item label="重排序后保留数量">
        <a-input-number
          v-model:value="formData.rerankConfig.topN"
          :min="1"
          :max="formData.topK"
          style="width: 100%"
          @change="handleChange"
        />
        <div class="form-hint">重排序后保留的文档数量，应小于等于 Top K</div>
      </a-form-item>
    </template>

    <!-- 元数据过滤配置 -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      元数据过滤
    </a-divider>

    <div class="metadata-filters">
      <div
        v-for="(filter, index) in formData.metadataFilters"
        :key="index"
        class="filter-item"
      >
        <a-row :gutter="8" align="middle">
          <a-col :span="7">
            <a-input
              v-model:value="filter.field"
              placeholder="字段名"
              size="small"
              @change="handleChange"
            />
          </a-col>
          <a-col :span="7">
            <a-select
              v-model:value="filter.operator"
              size="small"
              style="width: 100%"
              @change="handleChange"
            >
              <a-select-option value="EQUALS">等于</a-select-option>
              <a-select-option value="NOT_EQUALS">不等于</a-select-option>
              <a-select-option value="CONTAINS">包含</a-select-option>
              <a-select-option value="IN">在列表中</a-select-option>
              <a-select-option value="NOT_IN">不在列表中</a-select-option>
              <a-select-option value="GREATER_THAN">大于</a-select-option>
              <a-select-option value="LESS_THAN">小于</a-select-option>
            </a-select>
          </a-col>
          <a-col :span="7">
            <a-input
              v-model:value="filter.value"
              placeholder="值"
              size="small"
              @change="handleChange"
            />
          </a-col>
          <a-col :span="3">
            <a-button
              type="text"
              danger
              size="small"
              @click="removeFilter(index)"
            >
              <DeleteOutlined />
            </a-button>
          </a-col>
        </a-row>
      </div>

      <a-button type="dashed" size="small" block @click="addFilter">
        <PlusOutlined /> 添加过滤条件
      </a-button>
    </div>

    <a-divider style="margin: 16px 0 12px" />

    <a-row :gutter="16">
      <a-col :span="12">
        <a-form-item label="返回元数据">
          <a-switch
            v-model:checked="formData.includeMetadata"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col :span="12">
        <a-form-item label="返回相似度分数">
          <a-switch
            v-model:checked="formData.includeScore"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
    </a-row>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: knowledge_output"
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

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  .metadata-filters {
    .filter-item {
      margin-bottom: 8px;
    }
  }
}
</style>
