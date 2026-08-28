<script lang="ts" setup>
import type { KnowledgeBase } from './api';

import { computed, onMounted, ref } from 'vue';

import {
  BookOutlined,
  DatabaseOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { Card, message, Spin, Tag } from 'ant-design-vue';

import { getKnowledgeBasePage } from './api';

const emit = defineEmits<{
  select: [kb: KnowledgeBase];
}>();

const loading = ref(false);
const searchText = ref('');
const knowledgeBaseData = ref<KnowledgeBase[]>([]);
const selectedId = ref<null | number>(null);

// 过滤后的知识库列表
const filteredKnowledgeBases = computed(() => {
  if (!searchText.value.trim()) {
    return knowledgeBaseData.value;
  }
  const keyword = searchText.value.toLowerCase().trim();
  return knowledgeBaseData.value.filter(
    (kb) =>
      kb.name.toLowerCase().includes(keyword) ||
      (kb.description && kb.description.toLowerCase().includes(keyword)),
  );
});

// 获取知识库列表
const getKnowledgeBases = async () => {
  loading.value = true;
  try {
    const res = await getKnowledgeBasePage({ current: 1, size: 1000 });
    knowledgeBaseData.value = res.records || [];
    return knowledgeBaseData.value;
  } catch {
    message.error('获取知识库列表失败');
    return [];
  } finally {
    loading.value = false;
  }
};

// 选择知识库
const handleSelectKnowledgeBase = (kb: KnowledgeBase) => {
  selectedId.value = kb.id;
  emit('select', kb);
};

// 清空搜索
const handleClearSearch = () => {
  searchText.value = '';
};

onMounted(async () => {
  const list = await getKnowledgeBases();
  if (list.length > 0) {
    handleSelectKnowledgeBase(list[0]!);
  }
});

defineExpose({
  selectedId,
  getKnowledgeBases,
});
</script>

<template>
  <Card class="kb-list-card" :bordered="false">
    <template #title>
      <div class="flex items-center">
        <DatabaseOutlined class="mr-2 text-blue-500" />
        知识库列表
      </div>
    </template>

    <div class="kb-content">
      <!-- 搜索框 -->
      <div class="mb-4">
        <a-input-search
          v-model:value="searchText"
          placeholder="搜索知识库..."
          allow-clear
          @clear="handleClearSearch"
        >
          <template #prefix>
            <SearchOutlined class="text-gray-400" />
          </template>
        </a-input-search>
      </div>

      <!-- 知识库列表 -->
      <div class="knowledge-base-list">
        <Spin :spinning="loading">
          <div
            v-for="kb in filteredKnowledgeBases"
            :key="kb.id"
            class="knowledge-base-item"
            :class="{ selected: selectedId === kb.id }"
            @click="handleSelectKnowledgeBase(kb)"
          >
            <div class="kb-header">
              <BookOutlined class="mr-2 text-blue-500" />
              <span class="font-medium">{{ kb.name }}</span>
            </div>
            <div v-if="kb.description" class="kb-description">
              {{ kb.description }}
            </div>
            <div class="kb-meta">
              <Tag size="small" color="blue">TopK: {{ kb.topK || 5 }}</Tag>
              <Tag size="small" color="green">
                分数: {{ kb.scoreThreshold ?? 0.7 }}
              </Tag>
            </div>
          </div>

          <div
            v-if="filteredKnowledgeBases.length === 0 && !loading"
            class="empty-state"
          >
            <DatabaseOutlined class="mb-4 text-5xl text-gray-300" />
            <p v-if="searchText.trim()" class="text-gray-500">
              未找到包含"{{ searchText }}"的知识库
            </p>
            <div v-else class="text-gray-500">
              <p>暂无知识库</p>
              <p>请先创建知识库</p>
            </div>
          </div>
        </Spin>
      </div>
    </div>
  </Card>
</template>

<style lang="less" scoped>
.kb-list-card {
  display: flex;
  flex-direction: column;
  width: 280px;
  min-width: 280px;
  height: 100%;
  overflow: hidden;
  border-right: 1px solid #e8e8e8;
  border-radius: 8px 0 0 8px;

  :deep(.ant-card-head) {
    min-height: auto;
    padding: 12px 16px;
    border-bottom: 1px solid #f0f0f0;
  }

  :deep(.ant-card-body) {
    display: flex;
    flex: 1;
    flex-direction: column;
    padding: 12px;
    overflow: hidden;
  }
}

.kb-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}

.knowledge-base-list {
  flex: 1;
  padding-right: 4px;
  overflow-y: auto;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-thumb {
    background: #ccc;
    border-radius: 4px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }
}

.knowledge-base-item {
  padding: 10px;
  margin-bottom: 8px;
  cursor: pointer;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  transition: all 0.2s;

  &:hover {
    border-color: #1890ff;
    box-shadow: 0 2px 6px rgb(0 0 0 / 8%);
  }

  &.selected {
    background: #e6f7ff;
    border-color: #1890ff;
  }
}

.kb-header {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}

.kb-description {
  display: -webkit-box;
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 12px;
  line-height: 1.4;
  color: #666;
  -webkit-box-orient: vertical;
}

.kb-meta {
  display: flex;
  gap: 6px;
}

.empty-state {
  padding: 30px 15px;
  color: #999;
  text-align: center;
}
</style>
