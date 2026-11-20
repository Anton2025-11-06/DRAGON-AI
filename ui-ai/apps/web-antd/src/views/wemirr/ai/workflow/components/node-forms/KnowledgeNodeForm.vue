<script setup lang="ts">
/**
 * Knowledge 节点配置表单
 * 参数集对齐「知识库 → 知识检索」页面（后端 RagRetrievalService.search_detail 同一颗召回）：
 * 检索数量/阈值/模式与权重/限定模态/重排模型/元数据过滤/图谱增强。
 * 约定：留空的项一律不覆盖知识库自己的 retrieve_config（与检索页的「默认」同一语义），
 * 所以这里不设硬编码默认值去冒充用户的选择。
 */
import type {
  ChunkTypeFilter,
  FilterOperator,
  KnowledgeBaseOption,
  KnowledgeRetrievalConfig,
  MetadataFilter,
  RerankConfig,
  RetrievalMode,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';

import { listKnowledgeBases } from '#/api/ai-workflow';
import { MODEL_TYPE_RERANK, MODEL_TYPE_TEXT } from '#/api/ai-workflow/const';

import { ModelSelect } from '../model-select';
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

// 图谱那一路的取值区间（与检索页、后端 clamp_depth/clamp_scale 同一套上限）
const GRAPH_DEPTH_MIN = 1;
const GRAPH_DEPTH_MAX = 4;
const GRAPH_SCALE_MIN = 1;
const GRAPH_SCALE_MAX = 100;

/**
 * 元数据过滤可用字段：后端 _META_FIELDS 里真建了索引的那些。
 * 只有建了索引的字段能过滤（source 那种 index=False 的 JSON 串查了不报错、只静默命中 0 条），
 * 所以这里按后端清单写死，不给用户一个「填了没生效」的坑。
 */
const FILTER_FIELDS = [
  'kbId',
  'docId',
  'chunkId',
  'chunkIndex',
  'chunkType',
  'titlePath',
  'pageNum',
  'sheetName',
  'available',
  'createTime',
  'content',
];

/** 运算符与后端 _META_OPS 同一套 9 个取值（少一个就是画布上填了被忽略的条件） */
const FILTER_OPERATORS: { label: string; value: FilterOperator }[] = [
  { label: '等于', value: 'EQUALS' },
  { label: '不等于', value: 'NOT_EQUALS' },
  { label: '包含', value: 'CONTAINS' },
  { label: '在列表中', value: 'IN' },
  { label: '不在列表中', value: 'NOT_IN' },
  { label: '大于', value: 'GREATER_THAN' },
  { label: '大于等于', value: 'GREATER_OR_EQUAL' },
  { label: '小于', value: 'LESS_THAN' },
  { label: '小于等于', value: 'LESS_OR_EQUAL' },
];

// 默认重排序配置（topN 是 legacy 键，面板不再编辑，只在旧图上原样带回去）
const defaultRerankConfig: RerankConfig = {
  enabled: false,
  rerankModelId: undefined,
};

// 表单数据
const formData = reactive<
  KnowledgeRetrievalConfig & { rerankConfig: RerankConfig }
>({
  knowledgeBaseIds: [],
  queryVariable: '',
  topK: 5,
  scoreThreshold: undefined,
  retrievalMode: 'HYBRID' as RetrievalMode,
  vectorWeight: 0.7,
  chunkType: '' as ChunkTypeFilter,
  rerankConfig: { ...defaultRerankConfig },
  metadataFilters: [],
  withGraph: false,
  graphChatModelId: undefined,
  graphDepth: 2,
  graphScale: 10,
  graphSourceChunks: false,
  outputVariable: '',
  includeMetadata: true,
  includeScore: true,
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      knowledgeBaseIds: config.knowledgeBaseIds || [],
      queryVariable: config.queryVariable || '',
      topK: config.topK ?? 5,
      // 阈值/权重留空就是「用知识库配置」，不能拿 0 或 0.7 冒充用户填过
      scoreThreshold: config.scoreThreshold ?? undefined,
      retrievalMode: (config.retrievalMode ?? 'HYBRID') as RetrievalMode,
      vectorWeight: config.vectorWeight ?? 0.7,
      chunkType: (config.chunkType ?? '') as ChunkTypeFilter,
      rerankConfig: config.rerankConfig
        ? { ...defaultRerankConfig, ...config.rerankConfig }
        : { ...defaultRerankConfig },
      metadataFilters: config.metadataFilters || [],
      withGraph: config.withGraph ?? false,
      graphChatModelId: config.graphChatModelId ?? undefined,
      graphDepth: config.graphDepth ?? 2,
      graphScale: config.graphScale ?? 10,
      graphSourceChunks: config.graphSourceChunks ?? false,
      outputVariable: config.outputVariable || '',
      includeMetadata: config.includeMetadata ?? true,
      includeScore: config.includeScore ?? true,
    });
  },
  { immediate: true, deep: true },
);

/** 只有混合检索才谈得上两路权重（单选一路就是那一路 100%） */
const isHybrid = computed(() => formData.retrievalMode === 'HYBRID');
/** 开了图谱增强但没选对话模型：后端也会只留一句 warning，这里提前把话说在保存前 */
const graphWithoutModel = computed(
  () => !!formData.withGraph && !formData.graphChatModelId,
);
/** 启用了重排序但没选模型：执行时回到知识库上登记的重排模型（库上也没有就等于不重排） */
const rerankWithoutModel = computed(
  () => !!formData.rerankConfig.enabled && !formData.rerankConfig.rerankModelId,
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
    vectorWeight: isHybrid.value ? formData.vectorWeight : undefined,
    chunkType: formData.chunkType || undefined,
    // 重排序开关原样存：面板上开关显示的仓位必须等于真实行为
    // （关掉=本次明确不重排，不再偷偷沿用库配置）
    rerankConfig: { ...formData.rerankConfig },
    // 过滤条件整组原样带回（不按填没填过滤）：父级会把自己 emit 出去的 config 回灌给
    // props.config，这里一过滤，刚点「添加过滤条件」长出来的那行就在下一轮 watch 里被抹掉，
    // 界面上看着就是「点了没反应」（与 IfElse 表单同一口径）。空行在执行侧被当「没写完」跳过
    metadataFilters:
      formData.metadataFilters && formData.metadataFilters.length > 0
        ? formData.metadataFilters.map((f: MetadataFilter) => ({ ...f }))
        : undefined,
    withGraph: !!formData.withGraph,
    graphChatModelId: formData.withGraph
      ? formData.graphChatModelId
      : undefined,
    graphDepth: formData.withGraph ? formData.graphDepth : undefined,
    graphScale: formData.withGraph ? formData.graphScale : undefined,
    graphSourceChunks: !!formData.graphSourceChunks,
    outputVariable: formData.outputVariable,
    includeMetadata: formData.includeMetadata,
    includeScore: formData.includeScore,
  };
  emit('update:config', config);
}

onMounted(() => {
  loadKnowledgeBases();
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
        :max-tag-count="3"
        option-filter-prop="children"
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
      <div class="form-hint">
        执行时按工作流归属人的使用权限检索，没权限的库会被跳过并写进 warnings
      </div>
    </a-form-item>

    <a-form-item label="查询内容" required>
      <VariableInput
        v-model="formData.queryVariable"
        :current-node-id="nodeId"
        placeholder="选择要检索的文本变量，或直接写检索词"
        :filter-types="['string']"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="检索数量 (Top K)">
      <a-input-number
        v-model:value="formData.topK"
        :min="1"
        :max="100"
        placeholder="默认"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="form-hint">
        本次检索总共返回几条（跨库合并排序后截断），留空 = 用知识库配置
      </div>
    </a-form-item>

    <a-form-item>
      <template #label>
        <span>
          相似度阈值
          <a-tooltip
            title="低于该相似度的命中直接丢掉（融合分 0~1）；留空 = 用知识库自己的配置"
          >
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-input-number
        v-model:value="formData.scoreThreshold"
        :min="0"
        :max="1"
        :step="0.05"
        placeholder="默认"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item>
      <template #label>
        <span>
          检索模式
          <a-tooltip
            title="向量 = 语义召回，全文 = 关键词召回，混合 = 两路按权重融合；按知识库配置 = 每个库沿用自己的设置"
          >
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-select v-model:value="formData.retrievalMode" @change="handleChange">
        <a-select-option value="">按知识库配置</a-select-option>
        <a-select-option value="VECTOR">向量检索</a-select-option>
        <a-select-option value="FULLTEXT">全文检索</a-select-option>
        <a-select-option value="HYBRID">混合检索</a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item v-if="isHybrid">
      <template #label>
        <span>
          向量权重
          <a-tooltip
            title="混合检索时两路的占比：向量侧 = 该值，关键词侧 = 1 - 该值"
          >
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-slider
        v-model:value="formData.vectorWeight"
        :min="0"
        :max="1"
        :step="0.05"
        :marks="{ 0: '关键词', 0.5: '5:5', 1: '向量' }"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="限定模态">
      <a-select v-model:value="formData.chunkType" @change="handleChange">
        <a-select-option value="">不限（全部切片）</a-select-option>
        <a-select-option value="text">仅文本</a-select-option>
        <a-select-option value="image">仅图片</a-select-option>
        <a-select-option value="audio_video">仅音视频</a-select-option>
      </a-select>
      <div class="form-hint">
        只召回某一类切片，默认不限（与知识检索页一致）
      </div>
    </a-form-item>

    <!-- 重排序配置 -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      重排序
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          启用重排序
          <a-tooltip
            title="开启后由重排模型对候选结果重排（只对本次检索生效）；关掉 = 本次明确不重排"
          >
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
      <ModelSelect
        v-model:model-value="formData.rerankConfig.rerankModelId"
        :default-type="MODEL_TYPE_RERANK"
        placeholder="选择重排序模型"
        @change="handleChange"
      />
      <div v-if="rerankWithoutModel" class="warn-line">
        未选重排模型：本次将改用知识库上登记的重排模型（库上也没配就等于不重排）
      </div>
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
            <a-select
              v-model:value="filter.field"
              placeholder="字段"
              size="small"
              style="width: 100%"
              @change="handleChange"
            >
              <a-select-option v-for="f in FILTER_FIELDS" :key="f" :value="f">
                {{ f }}
              </a-select-option>
            </a-select>
          </a-col>
          <a-col :span="6">
            <a-select
              v-model:value="filter.operator"
              size="small"
              style="width: 100%"
              @change="handleChange"
            >
              <a-select-option
                v-for="op in FILTER_OPERATORS"
                :key="op.value"
                :value="op.value"
              >
                {{ op.label }}
              </a-select-option>
            </a-select>
          </a-col>
          <a-col :span="8">
            <a-input
              v-model:value="filter.value"
              placeholder="值（IN/NOT_IN 用逗号分隔）"
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
      <div class="form-hint">
        只能按已建索引的字段过滤；填了不认识的字段不会报错，但这条条件会被忽略并写进
        warnings（值空着等同于没写这条条件，不会拿空值去把结果清成 0 条）
      </div>
    </div>

    <!-- 图谱增强（知识检索页的「知识图谱增强」同一档，仅文档型知识库有效） -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      知识图谱增强
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          叠加图谱那一路
          <a-tooltip
            title="先让对话模型从查询里抽出实体和关系，再按实体、关系各查一路向量，按深度扩邻域、按规模取数，带出的结果叠加到切片召回上（LightRAG 式）。仅文档型知识库有效，开启必须选对话模型"
          >
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-switch v-model:checked="formData.withGraph" @change="handleChange" />
    </a-form-item>

    <template v-if="formData.withGraph">
      <ModelSelect
        v-model:model-value="formData.graphChatModelId"
        :default-type="MODEL_TYPE_TEXT"
        placeholder="选择图谱抽词用的对话模型"
        @change="handleChange"
      />
      <a-row :gutter="16">
        <a-col :span="12">
          <a-form-item label="邻域深度">
            <a-input-number
              v-model:value="formData.graphDepth"
              :min="GRAPH_DEPTH_MIN"
              :max="GRAPH_DEPTH_MAX"
              style="width: 100%"
              @change="handleChange"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="召回规模">
            <a-input-number
              v-model:value="formData.graphScale"
              :min="GRAPH_SCALE_MIN"
              :max="GRAPH_SCALE_MAX"
              style="width: 100%"
              @change="handleChange"
            />
          </a-form-item>
        </a-col>
      </a-row>

      <a-form-item>
        <template #label>
          <span>
            反查原文
            <a-tooltip
              title="默认关：图谱那一路只贡献精炼的实体+关系，原文交给关键词+向量那一路——反查切片会把上下文撑大、容易让下游模型失焦"
            >
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
            </a-tooltip>
          </span>
        </template>
        <a-switch
          v-model:checked="formData.graphSourceChunks"
          @change="handleChange"
        />
      </a-form-item>

      <div v-if="graphWithoutModel" class="warn-line">
        开启知识图谱增强必须选择对话模型：图谱那一路要先让大模型从查询里抽出实体和关系
      </div>
    </template>

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
    <div class="form-hint" style="margin-top: -8px">
      关掉只留正文：下游只是拼进提示词时能少一大截 token
    </div>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: documents"
        @change="handleChange"
      />
      <div class="form-hint">
        另附 text（拼好的资料段）、count（条数）、warnings（本次检索的过程说明）
      </div>
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

  .warn-line {
    margin-bottom: 12px;
    padding: 6px 8px;
    border-radius: 4px;
    background: #fffbe6;
    border: 1px solid #ffe58f;
    color: #ad6800;
    font-size: 12px;
    line-height: 18px;
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
