<script lang="ts" setup>
import { computed, nextTick, ref, watch } from 'vue';

import { FileTextOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import * as documentApi from './api';

defineOptions({
  name: 'PreviewChunkModal',
});

const props = defineProps<Props>();

const emit = defineEmits<Emits>();

interface Props {
  open: boolean;
  documentId: null | number;
  documentTitle?: string;
}

interface Emits {
  (e: 'update:open', value: boolean): void;
}

const loading = ref(false);
const chunks = ref<documentApi.PreviewChunkResp[]>([]);
const searchText = ref('');
const chunksListRef = ref<HTMLElement | null>(null);
const chunkItemRefs = ref<Map<number, HTMLElement>>(new Map());

// 过滤后的分块列表
const filteredChunks = computed(() => {
  if (!searchText.value.trim()) {
    return chunks.value;
  }
  const keyword = searchText.value.toLowerCase().trim();
  return chunks.value.filter((chunk) =>
    chunk.content.toLowerCase().includes(keyword),
  );
});

// 高亮文本函数
const highlightText = (text: string, keyword: string): string => {
  if (!keyword.trim()) {
    // 转义HTML特殊字符
    return text
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  // 转义HTML特殊字符
  const escapedText = text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  // 创建正则表达式，不区分大小写
  const regex = new RegExp(
    `(${keyword.replaceAll(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`)})`,
    'gi',
  );

  // 替换匹配的文本为高亮标记
  return escapedText.replace(regex, '<mark class="highlight">$1</mark>');
};

// 获取文档分块预览
const fetchPreviewChunks = async () => {
  if (!props.documentId) {
    return;
  }

  loading.value = true;
  chunks.value = [];

  try {
    const result = await documentApi.PreviewDocumentChunk(props.documentId);
    chunks.value = result || [];
    if (chunks.value.length === 0) {
      message.info('该文档暂无分块内容');
    }
  } catch (error) {
    console.error('获取文档分块预览失败:', error);
    message.error('获取文档分块预览失败');
  } finally {
    loading.value = false;
  }
};

// 监听弹窗打开
watch(
  () => props.open,
  (newVal) => {
    if (newVal && props.documentId) {
      fetchPreviewChunks();
      searchText.value = '';
    } else {
      chunks.value = [];
      searchText.value = '';
    }
  },
  { immediate: true },
);

// 关闭弹窗
const handleCancel = () => {
  emit('update:open', false);
};

// 清空搜索
const handleClearSearch = () => {
  searchText.value = '';
};

// 滚动到第一个匹配的分块
const scrollToFirstMatch = () => {
  if (!searchText.value.trim() || filteredChunks.value.length === 0) {
    return;
  }

  nextTick(() => {
    const firstChunk = filteredChunks.value[0];
    if (firstChunk) {
      const element = chunkItemRefs.value.get(firstChunk.id);
      if (element && chunksListRef.value) {
        element.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        });
        // 添加高亮动画效果
        element.classList.add('highlight-chunk');
        setTimeout(() => {
          element.classList.remove('highlight-chunk');
        }, 2000);
      }
    }
  });
};

// 监听搜索文本变化，自动定位
watch(
  () => searchText.value,
  () => {
    if (searchText.value.trim()) {
      scrollToFirstMatch();
    }
  },
);

// 设置分块元素引用
const setChunkRef = (el: any, chunkId: number) => {
  if (el && el instanceof HTMLElement) {
    chunkItemRefs.value.set(chunkId, el);
  } else {
    chunkItemRefs.value.delete(chunkId);
  }
};
</script>

<template>
  <a-modal
    :open="open"
    :title="`文档分块预览${documentTitle ? ` - ${documentTitle}` : ''}`"
    width="1000px"
    :footer="null"
    @cancel="handleCancel"
  >
    <a-spin :spinning="loading">
      <div class="chunk-preview-container">
        <!-- 搜索框 -->
        <div class="search-bar">
          <a-input-search
            v-model:value="searchText"
            placeholder="搜索分块内容..."
            allow-clear
            @clear="handleClearSearch"
            @search="scrollToFirstMatch"
            @press-enter="scrollToFirstMatch"
          >
            <template #prefix>
              <SearchOutlined />
            </template>
          </a-input-search>
          <div class="chunk-count">共 {{ filteredChunks.length }} 个分块</div>
        </div>

        <!-- 分块列表 -->
        <div
          v-if="!loading && filteredChunks.length > 0"
          ref="chunksListRef"
          class="chunks-list"
        >
          <div
            v-for="(chunk, index) in filteredChunks"
            :key="chunk.id"
            :ref="(el) => setChunkRef(el, chunk.id)"
            class="chunk-item"
          >
            <div class="chunk-header">
              <a-tag color="blue">分块 #{{ index + 1 }}</a-tag>
              <a-tag color="default">ID: {{ chunk.id }}</a-tag>
            </div>
            <div class="chunk-content">
              <pre
                class="content-text"
                v-html="highlightText(chunk.content, searchText.trim())"
              ></pre>
            </div>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-else-if="!loading" class="empty-state">
          <FileTextOutlined class="empty-icon" />
          <p v-if="searchText.trim()" class="empty-text">
            未找到包含"{{ searchText }}"的分块
          </p>
          <p v-else class="empty-text">暂无分块内容</p>
        </div>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
@keyframes highlight-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgb(24 144 255 / 20%);
  }

  50% {
    box-shadow: 0 0 0 4px rgb(24 144 255 / 40%);
  }
}

.chunk-preview-container {
  display: flex;
  flex-direction: column;
  min-height: 400px;
  max-height: 70vh;
}

.search-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.chunk-count {
  font-size: 14px;
  color: #666;
  white-space: nowrap;
}

.chunks-list {
  flex: 1;
  padding-right: 4px;
  overflow-y: auto;
}

.chunk-item {
  padding: 16px;
  margin-bottom: 16px;
  background: #fafafa;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  transition: all 0.3s;
}

.chunk-item:hover {
  border-color: #1890ff;
  box-shadow: 0 2px 8px rgb(0 0 0 / 10%);
}

.chunk-item.highlight-chunk {
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgb(24 144 255 / 20%);
  animation: highlight-pulse 2s ease-in-out;
}

.chunk-header {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.chunk-content {
  padding: 12px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
}

.content-text {
  padding: 0;
  margin: 0;
  font-family:
    Monaco, Menlo, 'Ubuntu Mono', Consolas, source-code-pro, monospace;
  font-size: 13px;
  line-height: 1.8;
  color: #333;
  word-wrap: break-word;
  white-space: pre-wrap;
  background: transparent;
  border: none;
}

.content-text :deep(.highlight) {
  padding: 2px 4px;
  font-weight: 500;
  color: #333;
  background-color: #fff566;
  border-radius: 3px;
  box-shadow: 0 1px 2px rgb(0 0 0 / 10%);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  color: #999;
}

.empty-icon {
  margin-bottom: 16px;
  font-size: 64px;
  color: #d9d9d9;
}

.empty-text {
  margin: 0;
  font-size: 16px;
  color: #999;
}

/* 滚动条样式 */
.chunks-list::-webkit-scrollbar {
  width: 6px;
}

.chunks-list::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 3px;
}

.chunks-list::-webkit-scrollbar-thumb {
  background: #c1c1c1;
  border-radius: 3px;
}

.chunks-list::-webkit-scrollbar-thumb:hover {
  background: #a8a8a8;
}
</style>
