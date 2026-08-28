<script lang="ts" setup>
/**
 * 模板选择弹窗组件
 * 用于创建新工作流时选择模板
 */
import type {
  TemplateCategory,
  WorkflowTemplateResp,
} from '#/api/ai-workflow/types';

import { computed, onMounted, ref, watch } from 'vue';

import {
  AppstoreOutlined,
  BookOutlined,
  DatabaseOutlined,
  EditOutlined,
  FileAddOutlined,
  FileTextOutlined,
  MessageOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { Input, message, Spin, Tabs } from 'ant-design-vue';

import { getTemplatePage } from '#/api/ai-workflow';

interface Props {
  open: boolean;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'select', template: WorkflowTemplateResp | null): void;
}>();

// 模板列表
const templates = ref<WorkflowTemplateResp[]>([]);
const loading = ref(false);
const searchKeyword = ref('');
const activeCategory = ref('all');

// 分类配置
const categories = [
  { key: 'all', label: '全部', icon: AppstoreOutlined },
  { key: 'RAG', label: 'RAG问答', icon: BookOutlined },
  { key: 'SUMMARY', label: '文档摘要', icon: FileTextOutlined },
  { key: 'EXTRACTION', label: '数据提取', icon: DatabaseOutlined },
  { key: 'CONVERSATION', label: '多轮对话', icon: MessageOutlined },
  { key: 'GENERATION', label: '内容生成', icon: EditOutlined },
];

// 分类图标映射
const categoryIcons: Record<TemplateCategory, any> = {
  RAG: BookOutlined,
  SUMMARY: FileTextOutlined,
  EXTRACTION: DatabaseOutlined,
  CONVERSATION: MessageOutlined,
  GENERATION: EditOutlined,
  CUSTOM: AppstoreOutlined,
};

// 过滤后的模板列表
const filteredTemplates = computed(() => {
  let result = templates.value;

  // 按分类过滤
  if (activeCategory.value !== 'all') {
    result = result.filter((t) => t.category === activeCategory.value);
  }

  // 按关键词过滤
  if (searchKeyword.value) {
    const keyword = searchKeyword.value.toLowerCase();
    result = result.filter(
      (t) =>
        t.name.toLowerCase().includes(keyword) ||
        t.description?.toLowerCase().includes(keyword),
    );
  }

  return result;
});

// 加载模板列表
async function loadTemplates() {
  loading.value = true;
  try {
    // 获取内置模板和用户自定义模板
    const res = await getTemplatePage({ size: 100 });
    templates.value = res.records;
  } catch {
    message.error('加载模板失败');
  } finally {
    loading.value = false;
  }
}

// 选择模板
function handleSelect(template: WorkflowTemplateResp) {
  emit('select', template);
  emit('update:open', false);
}

// 创建空白工作流
function handleCreateBlank() {
  emit('select', null);
  emit('update:open', false);
}

// 关闭弹窗
function handleCancel() {
  emit('update:open', false);
}

// 获取分类图标
function getCategoryIcon(category: TemplateCategory) {
  return categoryIcons[category] || AppstoreOutlined;
}

// 监听弹窗打开
watch(
  () => props.open,
  (open) => {
    if (open) {
      loadTemplates();
    }
  },
);

onMounted(() => {
  if (props.open) {
    loadTemplates();
  }
});
</script>

<template>
  <a-modal
    :open="open"
    title="选择模板"
    :width="800"
    :footer="null"
    @cancel="handleCancel"
  >
    <Spin :spinning="loading">
      <div class="template-select-modal">
        <!-- 搜索框 -->
        <div class="search-bar">
          <Input
            v-model:value="searchKeyword"
            placeholder="搜索模板..."
            allow-clear
          >
            <template #prefix>
              <SearchOutlined />
            </template>
          </Input>
        </div>

        <!-- 分类标签 -->
        <Tabs v-model:activeKey="activeCategory" class="category-tabs">
          <a-tab-pane v-for="cat in categories" :key="cat.key">
            <template #tab>
              <span class="tab-label">
                <component :is="cat.icon" />
                {{ cat.label }}
              </span>
            </template>
          </a-tab-pane>
        </Tabs>

        <!-- 模板网格 -->
        <div class="template-grid">
          <!-- 空白模板卡片 -->
          <div class="template-item blank-template" @click="handleCreateBlank">
            <div class="icon-wrapper">
              <FileAddOutlined />
            </div>
            <div class="info">
              <div class="name">空白工作流</div>
              <div class="desc">从零开始创建工作流</div>
            </div>
          </div>

          <!-- 模板卡片 -->
          <div
            v-for="template in filteredTemplates"
            :key="template.id"
            class="template-item"
            @click="handleSelect(template)"
          >
            <div class="icon-wrapper" :class="template.category.toLowerCase()">
              <component :is="getCategoryIcon(template.category)" />
            </div>
            <div class="info">
              <div class="name">
                {{ template.name }}
                <a-tag v-if="template.builtIn" color="gold" size="small">
                  内置
                </a-tag>
              </div>
              <div class="desc">{{ template.description || '暂无描述' }}</div>
              <div class="meta">{{ template.nodeCount || 0 }} 个节点</div>
            </div>
          </div>
        </div>

        <!-- 空状态 -->
        <div
          v-if="!loading && filteredTemplates.length === 0"
          class="empty-state"
        >
          <p>没有找到匹配的模板</p>
        </div>
      </div>
    </Spin>
  </a-modal>
</template>

<style lang="less" scoped>
.template-select-modal {
  .search-bar {
    margin-bottom: 16px;
  }

  .category-tabs {
    margin-bottom: 16px;

    .tab-label {
      display: flex;
      gap: 6px;
      align-items: center;
    }
  }

  .template-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
    max-height: 400px;
    overflow-y: auto;
  }

  .template-item {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    padding: 12px;
    cursor: pointer;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    transition: all 0.2s;

    &:hover {
      border-color: #1890ff;
      background: #f5f5f5;
    }

    &.blank-template {
      .icon-wrapper {
        color: #1890ff;
        background: #e6f7ff;
      }
    }

    .icon-wrapper {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      font-size: 20px;
      color: #666;
      background: #f5f5f5;
      border-radius: 8px;

      &.rag {
        color: #1890ff;
        background: #e6f7ff;
      }

      &.summary {
        color: #52c41a;
        background: #f6ffed;
      }

      &.extraction {
        color: #fa8c16;
        background: #fff7e6;
      }

      &.conversation {
        color: #722ed1;
        background: #f9f0ff;
      }

      &.generation {
        color: #13c2c2;
        background: #e6fffb;
      }
    }

    .info {
      flex: 1;
      min-width: 0;

      .name {
        display: flex;
        gap: 6px;
        align-items: center;
        margin-bottom: 4px;
        font-size: 14px;
        font-weight: 500;
        color: #333;
      }

      .desc {
        display: -webkit-box;
        margin-bottom: 4px;
        overflow: hidden;
        font-size: 12px;
        line-height: 1.4;
        color: #666;
        text-overflow: ellipsis;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
      }

      .meta {
        font-size: 12px;
        color: #999;
      }
    }
  }

  .empty-state {
    padding: 40px;
    text-align: center;
    color: #999;
  }
}
</style>
