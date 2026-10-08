<script lang="ts" setup>
/**
 * 解析后文档预览：只读，取 /documents/{id}/content（后端按配置截断前若干切片拼接）。
 *
 * 需求 13 把文档里的图片/音视频上传到存储并把地址回填回原位置，正文因此是一份 Markdown：
 * 拿 <pre> 直接堆屏只会看到一排 `![](https://...)` 标记，所以默认走渲染视图，
 * 同时留一个切回原文的开关（排查解析产物时还得看原始标记）。
 */
import { computed, ref, watch } from 'vue';

import { message, Modal, Segmented, Spin } from 'ant-design-vue';

import MarkdownRenderer from '../../workflow/components/debug/MarkdownRenderer.vue';
import { GetDocContent } from './api';

const props = defineProps<{
  docId: number | null;
  open: boolean;
  title?: string;
}>();
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>();

const loading = ref(false);
const content = ref('');
const truncated = ref(false);
// 默认看渲染结果（图片能真显示出来），需要核对解析产物时再切原文
const viewMode = ref<'markdown' | 'render'>('render');
const viewOptions = [
  { label: '渲染视图', value: 'render' },
  { label: 'Markdown 原文', value: 'markdown' },
];
const sizeText = computed(() =>
  content.value ? `${content.value.length.toLocaleString()} 字` : '',
);

watch(
  () => props.open,
  async (open) => {
    if (!open || !props.docId) return;
    loading.value = true;
    content.value = '';
    try {
      const res = await GetDocContent(props.docId);
      content.value = res?.content || '';
      truncated.value = !!res?.truncated;
    } catch (e: any) {
      message.error(e?.message || '加载失败');
    } finally {
      loading.value = false;
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    :footer="null"
    :width="860"
    :title="`解析后文档 - ${title || ''}`"
    @update:open="emit('update:open', $event)"
  >
    <Spin :spinning="loading">
      <div class="preview-bar">
        <Segmented v-model:value="viewMode" :options="viewOptions" size="small" />
        <span v-if="sizeText" class="tip">{{ sizeText }}</span>
      </div>
      <div v-if="truncated" class="tip">内容过长，已按配置截断显示</div>
      <div v-if="viewMode === 'render'" class="content render">
        <MarkdownRenderer :content="content" />
        <div v-if="!content" class="content">（暂无内容）</div>
      </div>
      <pre v-else class="content">{{ content || '（暂无内容）' }}</pre>
    </Spin>
  </Modal>
</template>

<style scoped>
.tip {
  margin-bottom: 8px;
  font-size: 12px;
  color: #fa8c16;
}

.preview-bar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.preview-bar .tip {
  margin-bottom: 0;
  color: var(--text-color-secondary, #999);
}

/* 渲染视图里图片/表格可能比弹窗宽，锁住宽度而不是拉横滚动条 */
.render :deep(img),
.render :deep(video),
.render :deep(table) {
  max-width: 100%;
}

.content {
  max-height: 60vh;
  padding: 12px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  background: var(--component-background-light, #fafafa);
  border-radius: 4px;
}
</style>
