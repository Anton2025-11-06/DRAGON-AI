<script lang="ts" setup>
/**
 * 语义搜索抽屉组件
 * 用于测试知识库的语义召回效果
 */
import { ref, watch } from 'vue';

import { useVbenDrawer } from '@vben/common-ui';

import { SearchOutlined, ThunderboltOutlined } from '@ant-design/icons-vue';
import {
  Collapse,
  CollapsePanel,
  InputNumber,
  InputSearch,
  message,
  Spin,
  Tag,
} from 'ant-design-vue';

import type { EmbeddingMatchRep } from './api';
import { SemanticSearch } from './api';

const [Drawer, drawerApi] = useVbenDrawer({
  footer: false,
  onOpenChange(isOpen) {
    if (isOpen) {
      const data = drawerApi.getData<{ knowledgeBase: any }>();
      if (data?.knowledgeBase) {
        knowledgeBase.value = data.knowledgeBase;
        topK.value = data.knowledgeBase.topK || 5;
      }
      searchQuery.value = '';
      searchResults.value = [];
    }
  },
});

const loading = ref(false);
const knowledgeBase = ref<any>(null);
const searchQuery = ref('');
const topK = ref(5);
const searchResults = ref<EmbeddingMatchRep[]>([]);

/** 执行语义搜索 */
const handleSearch = async () => {
  if (!knowledgeBase.value) {
    message.warning('请先选择知识库');
    return;
  }
  if (!searchQuery.value.trim()) {
    message.warning('请输入搜索内容');
    return;
  }

  loading.value = true;
  searchResults.value = [];

  try {
    const results = await SemanticSearch(
      knowledgeBase.value.id,
      searchQuery.value.trim(),
      topK.value,
    );
    searchResults.value = results || [];
    if (searchResults.value.length === 0) {
      message.info('未找到匹配的内容');
    }
  } catch (error) {
    console.error('语义搜索失败:', error);
    message.error('语义搜索失败');
  } finally {
    loading.value = false;
  }
};

const formatScore = (score: number) => (score ? score.toFixed(4) : '-');

const formatMetadata = (metadata: Record<string, any>) => {
  if (!metadata || Object.keys(metadata).length === 0) return null;
  return JSON.stringify(metadata, null, 2);
};

const drawerTitle = ref('语义搜索');
watch(
  knowledgeBase,
  (kb) => {
    drawerTitle.value = kb ? `语义搜索 - ${kb.name}` : '语义搜索';
  },
  { immediate: true },
);

defineExpose({ drawerApi });
</script>

<template>
  <Drawer class="w-1/2" :title="drawerTitle">
    <Spin :spinning="loading">
      <div class="search-container">
        <div class="search-bar">
          <InputSearch
            v-model:value="searchQuery"
            placeholder="请输入搜索内容..."
            size="large"
            allow-clear
            @search="handleSearch"
            @press-enter="handleSearch"
          >
            <template #prefix>
              <SearchOutlined />
            </template>
            <template #enterButton>
              <span class="flex items-center gap-1">
                <ThunderboltOutlined />
                搜索
              </span>
            </template>
          </InputSearch>
          <div class="search-params">
            <span class="param-label">TopK:</span>
            <InputNumber
              v-model:value="topK"
              :min="1"
              :max="50"
              :precision="0"
              size="small"
              style="width: 80px"
            />
          </div>
        </div>

        <div v-if="!loading && searchResults.length > 0" class="results-list">
          <div
            v-for="(result, index) in searchResults"
            :key="index"
            class="result-item"
          >
            <div class="result-header">
              <Tag color="blue">结果 #{{ index + 1 }}</Tag>
              <Tag color="green">相似度: {{ formatScore(result.score) }}</Tag>
              <Tag v-if="result.searchType" color="default">
                {{ result.searchType }}
              </Tag>
            </div>
            <div class="result-content">
              <pre class="content-text">{{ result.content }}</pre>
            </div>
            <div v-if="formatMetadata(result.metadata)" class="result-metadata">
              <Collapse>
                <CollapsePanel key="metadata" header="元数据">
                  <pre class="metadata-text">{{
                    formatMetadata(result.metadata)
                  }}</pre>
                </CollapsePanel>
              </Collapse>
            </div>
          </div>
        </div>

        <div v-else-if="!loading" class="empty-state">
          <SearchOutlined class="empty-icon" />
          <p v-if="searchQuery.trim()" class="empty-text">
            未找到匹配的内容，请尝试其他关键词
          </p>
          <p v-else class="empty-text">请输入搜索内容进行语义搜索</p>
        </div>
      </div>
    </Spin>
  </Drawer>
</template>

<style scoped>
.search-container {
  display: flex;
  flex-direction: column;
  min-height: 400px;
}

.search-bar {
  margin-bottom: 16px;
}

.search-params {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}

.param-label {
  font-size: 14px;
  color: var(--text-color-secondary, #666);
}

.results-list {
  flex: 1;
  padding-right: 4px;
  overflow-y: auto;
}

.result-item {
  padding: 16px;
  margin-bottom: 16px;
  background: var(--component-background-light, #fafafa);
  border: 1px solid var(--border-color, #e8e8e8);
  border-radius: 6px;
  transition: all 0.3s;
}

.result-item:hover {
  border-color: hsl(var(--primary));
  box-shadow: 0 2px 8px rgb(0 0 0 / 10%);
}

.result-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.result-content {
  padding: 12px;
  margin-bottom: 8px;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color-light, #f0f0f0);
  border-radius: 4px;
}

.content-text {
  padding: 0;
  margin: 0;
  font-family: Monaco, Menlo, 'Ubuntu Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.8;
  color: var(--text-color, #333);
  word-wrap: break-word;
  white-space: pre-wrap;
  background: transparent;
  border: none;
}

.result-metadata {
  margin-top: 8px;
}

.metadata-text {
  padding: 0;
  margin: 0;
  font-family: Monaco, Menlo, 'Ubuntu Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-color-secondary, #666);
  word-wrap: break-word;
  white-space: pre-wrap;
  background: transparent;
  border: none;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  color: var(--text-color-secondary, #999);
}

.empty-icon {
  margin-bottom: 16px;
  font-size: 64px;
  opacity: 0.5;
}

.empty-text {
  margin: 0;
  font-size: 16px;
}
</style>
