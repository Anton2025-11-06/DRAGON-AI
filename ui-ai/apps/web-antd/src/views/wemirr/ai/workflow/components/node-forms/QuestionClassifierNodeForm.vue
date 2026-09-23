<script setup lang="ts">
import type {
  ClassCategory,
  QuestionClassifierConfig,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import {
  DeleteOutlined,
  HolderOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';
import draggable from 'vuedraggable';

/**
 * 问题分类器节点配置表单
 * 使用 LLM（文生文）对问题进行智能分类，路由到不同的处理分支
 * 【设计对齐 2026-09】模型固定为 text_to_text，「直连/后缀」概念已废弃
 */
import { CHAT_TYPE_OPTIONS, MT_TEXT_TO_TEXT } from '#/api/ai-workflow/const';

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
  return `class_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
}

// 默认分类类别：每次给新对象，避开模块级共享行被多个节点互相改掉
function createDefaultCategories(): ClassCategory[] {
  return [
    { id: 'category_1', name: '类别1', description: '' },
    { id: 'category_2', name: '类别2', description: '' },
  ];
}

type QuestionClassifierFormData = Omit<
  QuestionClassifierConfig,
  'categories'
> & {
  categories: ClassCategory[];
};

// 表单数据
const formData = reactive<QuestionClassifierFormData>({
  modelId: undefined,
  modelType: MT_TEXT_TO_TEXT,
  inputVariable: '',
  instructions: '',
  categories: createDefaultCategories(),
  advancedMode: false,
  customPromptTemplate: '',
});

/**
 * 本地刚 emit 出去的配置快照（nodeId + 配置内容），与参数提取器同一套守卫口径。
 * 父层（PropertyPanel / selectedNode.data）会把自己刚收到的配置原样回传，不拦住的话
 * watch 会用全新对象重建 formData.categories，加上 handleChange 还按 id/name 过滤行，
 * 清空「类别ID」重打时整行会当场消失（行被卸载），ant-design-vue vc-input 在 nextTick
 * 里读已销毁实例的 inputRef 还会抛「Cannot read properties of null (reading 'input')」。
 */
let emittedSignature = '';

/** 配置内容签名（含类别行 id，既识别自己的回传，也不吞单字段的外部变更） */
function signatureOf(config: QuestionClassifierConfig | undefined): string {
  return JSON.stringify([
    config?.modelId ?? null,
    config?.inputVariable || '',
    config?.instructions || '',
    !!config?.advancedMode,
    config?.customPromptTemplate ?? null,
    (config?.categories || []).map((c) => [
      c.id || '',
      c.name || '',
      c.description || '',
      c.examples || [],
    ]),
  ]);
}

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    if (`${props.nodeId}|${signatureOf(config)}` === emittedSignature) {
      // 自己刚 emit 的内容，保留本地编辑状态（含未填完的类别行）与行对象身份
      return;
    }
    emittedSignature = '';
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MT_TEXT_TO_TEXT,
      inputVariable: config.inputVariable || '',
      instructions: config.instructions || '',
      categories:
        config.categories && config.categories.length > 0
          ? config.categories.map((c) => ({ ...c, id: c.id || generateId() }))
          : createDefaultCategories(),
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
    modelType: MT_TEXT_TO_TEXT,
    inputVariable: formData.inputVariable,
    instructions: formData.instructions,
    // 空 id / 空名的类别行不写进图（它决定输出端口与分支路由），但本地保留正在编辑的那一行
    categories: formData.categories
      .filter((c: ClassCategory) => c.id && c.name)
      // 拷一份再 emit：不把 reactive 行对象直接交给父层与持久化配置
      .map((c: ClassCategory) => ({ ...c })),
    advancedMode: formData.advancedMode,
    customPromptTemplate: formData.advancedMode
      ? formData.customPromptTemplate
      : undefined,
  };
  emittedSignature = `${props.nodeId}|${signatureOf(config)}`;
  emit('update:config', config);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      :type-options="CHAT_TYPE_OPTIONS"
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
    <a-divider orientation="left" style="margin: 16px 0 12px; font-size: 12px">
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
    <a-divider orientation="left" style="margin: 16px 0 12px; font-size: 12px">
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
          placeholder="自定义分类提示词模板，使用 {{input}} 引用输入文本，{{categories}} 引用类别列表"
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
