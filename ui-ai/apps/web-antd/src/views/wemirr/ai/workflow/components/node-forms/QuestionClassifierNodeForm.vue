<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      v-model:suffix-value="formData.suffix"
      v-model:model-is-direct="formData.modelIsDirect"
      :type-options="LLM_TYPE_OPTIONS"
      placeholder="选择用于分类的模型"
      @change="handleChange"
    />

    <a-form-item label="输入变量" required>
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        placeholder="选择要分类的文本变量"
        :filter-types="['string']"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="分类指导说明">
      <a-textarea
        v-model:value="formData.instructions"
        :rows="3"
        placeholder="描述分类的目的和标准，帮助模型更准确地分类"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 分类类别管理 -->
    <a-divider orientation="left" style=" margin: 16px 0 12px;font-size: 12px">
      分类类别
    </a-divider>

    <div class="categories-section">
      <draggable
        v-model="formData.categories"
        item-key="id"
        handle=".drag-handle"
        @change="handleChange"
      >
        <template #item="{ element: category, index }">
          <div class="category-item">
            <div class="category-header">
              <HolderOutlined class="drag-handle" />
              <span class="category-index">类别 {{ index + 1 }}</span>
              <a-button
                type="text"
                danger
                size="small"
                :disabled="formData.categories.length <= 2"
                @click="removeCategory(index)"
              >
                <DeleteOutlined />
              </a-button>
            </div>
            <div class="category-content">
              <a-row :gutter="8">
                <a-col :span="8">
                  <a-input
                    v-model:value="category.id"
                    placeholder="类别ID"
                    size="small"
                    @change="handleChange"
                  />
                </a-col>
                <a-col :span="16">
                  <a-input
                    v-model:value="category.name"
                    placeholder="类别名称"
                    size="small"
                    @change="handleChange"
                  />
                </a-col>
              </a-row>
              <a-textarea
                v-model:value="category.description"
                :rows="2"
                placeholder="类别描述（帮助模型理解分类标准）"
                size="small"
                style="margin-top: 8px"
                @change="handleChange"
              />
            </div>
          </div>
        </template>
      </draggable>

      <a-button type="dashed" size="small" block @click="addCategory">
        <PlusOutlined /> 添加分类类别
      </a-button>
    </div>

    <div class="form-hint" style="margin-top: 8px">
      每个分类类别将生成一个独立的输出端口，用于连接不同的处理分支
    </div>

    <!-- 高级模式 -->
    <a-divider orientation="left" style=" margin: 16px 0 12px;font-size: 12px">
      高级设置
    </a-divider>

    <a-form-item>
      <template #label>
        <span>
          高级模式
          <a-tooltip title="启用后可自定义分类提示词模板">
            <QuestionCircleOutlined style="margin-left: 4px; color: #8c8c8c" />
          </a-tooltip>
        </span>
      </template>
      <a-switch
        v-model:checked="formData.advancedMode"
        @change="handleChange"
      />
    </a-form-item>

    <template v-if="formData.advancedMode">
      <a-form-item label="自定义提示词模板">
        <a-textarea
          v-model:value="formData.customPromptTemplate"
          :rows="6"
          :placeholder="'自定义分类提示词模板，使用 {{input}} 引用输入文本，{{categories}} 引用类别列表'"
          @change="handleChange"
        />
        <div class="form-hint">
          可用变量: <code v-pre>{{ input }}</code> - 输入文本,
          <code v-pre>{{ categories }}</code> - 类别列表
        </div>
      </a-form-item>
    </template>
  </a-form>
</template>

<script setup lang="ts">
/**
 * 问题分类器节点配置表单
 * 使用 LLM 对问题进行智能分类，路由到不同的处理分支
 */
import { LLM_TYPE_OPTIONS, MODEL_TYPE_TEXT_GEN } from '#/api/ai-workflow/const';
import type {
  ClassCategory,
  QuestionClassifierConfig,
} from '#/api/ai-workflow/types';

import {
  DeleteOutlined,
  HolderOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';
import { reactive, ref, watch } from 'vue';
import draggable from 'vuedraggable';

import { ModelSelect } from '../model-select';
import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: QuestionClassifierConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: QuestionClassifierConfig): void;
}>();

// 生成唯一ID
function generateId(): string {
  return `class_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
}

// 默认分类类别
const defaultCategories: ClassCategory[] = [
  { id: 'category_1', name: '类别1', description: '' },
  { id: 'category_2', name: '类别2', description: '' },
];

type QuestionClassifierFormData = Omit<
  QuestionClassifierConfig,
  'categories'
> & {
  categories: ClassCategory[];
};

// 表单数据
const formData = reactive<QuestionClassifierFormData>({
  modelId: undefined,
  modelType: MODEL_TYPE_TEXT_GEN,
  suffix: undefined,
  modelIsDirect: undefined,
  inputVariable: '',
  instructions: '',
  categories: [...defaultCategories],
  advancedMode: false,
  customPromptTemplate: '',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MODEL_TYPE_TEXT_GEN,
      suffix: config.suffix,
      modelIsDirect: config.modelIsDirect,
      inputVariable: config.inputVariable || '',
      instructions: config.instructions || '',
      categories:
        config.categories && config.categories.length > 0
          ? config.categories.map((c) => ({ ...c }))
          : [...defaultCategories],
      advancedMode: config.advancedMode ?? false,
      customPromptTemplate: config.customPromptTemplate || '',
    });
  },
  { immediate: true, deep: true },
);

// 添加分类类别
function addCategory() {
  const newIndex = formData.categories.length + 1;
  formData.categories.push({
    id: generateId(),
    name: `类别${newIndex}`,
    description: '',
  });
  handleChange();
}

// 移除分类类别
function removeCategory(index: number) {
  if (formData.categories.length > 2) {
    formData.categories.splice(index, 1);
    handleChange();
  }
}

// 处理配置变更
function handleChange() {
  const config: QuestionClassifierConfig = {
    modelId: formData.modelId,
    modelType: formData.modelType,
    suffix: formData.suffix || undefined,
    modelIsDirect: formData.modelIsDirect,
    inputVariable: formData.inputVariable,
    instructions: formData.instructions,
    categories: formData.categories.filter(
      (c: ClassCategory) => c.id && c.name,
    ),
    advancedMode: formData.advancedMode,
    customPromptTemplate: formData.advancedMode
      ? formData.customPromptTemplate
      : undefined,
  };
  emit('update:config', config);
}
</script>
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

  .categories-section {
    .category-item {
      margin-bottom: 12px;
      padding: 12px;
      background: #fafafa;
      border: 1px solid #f0f0f0;
      border-radius: 6px;

      .category-header {
        display: flex;
        align-items: center;
        margin-bottom: 8px;

        .drag-handle {
          cursor: move;
          color: #bfbfbf;
          margin-right: 8px;

          &:hover {
            color: #1890ff;
          }
        }

        .category-index {
          flex: 1;
          font-size: 12px;
          font-weight: 500;
          color: #595959;
        }
      }

      .category-content {
        :deep(.ant-input) {
          font-size: 12px;
        }

        :deep(.ant-input-textarea) {
          font-size: 12px;
        }
      }
    }
  }
}
</style>
