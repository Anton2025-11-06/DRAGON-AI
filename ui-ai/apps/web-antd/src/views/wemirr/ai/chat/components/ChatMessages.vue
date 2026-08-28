<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';

import {
  CopyOutlined,
  FileTextOutlined,
  GlobalOutlined,
  LinkOutlined,
  RedoOutlined,
  RobotOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { Avatar, Button, message } from 'ant-design-vue';
import {
  MarkdownCodeBlockNode,
  MarkdownRender,
  setCustomComponents,
} from 'markstream-vue';

import 'markstream-vue/index.css';

import { referenceDisplayIndex, type ChatReference } from '../../shared';

// ==================== Props ====================
const props = defineProps<{
  isStreaming?: boolean;
  messages: ChatMessage[];
  streamingContent?: string;
  streamingReferences?: ChatReference[];
  streamingThinking?: string;
}>();

// ==================== Emits ====================
const emit = defineEmits<{
  retry: [content: string];
}>();

// 注册代码块渲染器
setCustomComponents('ai-chat', {
  code_block: MarkdownCodeBlockNode,
});

// ==================== 类型定义 ====================
export interface ChatMessage {
  key: string;
  role: 'ai' | 'user';
  content: string;
  references?: ChatReference[];
  thinking?: string;
}

// ==================== Refs ====================
const containerRef = ref<HTMLElement | null>(null);

// ==================== 操作处理 ====================
function handleCopy(content: string) {
  navigator.clipboard.writeText(content);
  message.success('已复制到剪贴板');
}

function handleRetry() {
  const lastUserMsg = props.messages.findLast((m) => m.role === 'user');
  if (lastUserMsg?.content) {
    emit('retry', lastUserMsg.content);
  }
}

// ==================== 显示内容 ====================
const displayMessages = computed(() => {
  // 深拷贝消息数组，避免直接修改原始数据
  const items = props.messages.map((msg) => ({ ...msg }));

  // 流式输出时更新最后一条 AI 消息内容
  if (props.isStreaming && items.length > 0) {
    const lastItem = items[items.length - 1];
    if (lastItem?.role === 'ai') {
      lastItem.content = props.streamingContent || '思考中...';
      lastItem.thinking = props.streamingThinking || lastItem.thinking;
      lastItem.references = props.streamingReferences || lastItem.references;
    }
  }

  return items;
});

// ==================== 滚动到底部 ====================
let scrollTimer: null | ReturnType<typeof setTimeout> = null;

function scrollToBottom() {
  if (scrollTimer) return;
  scrollTimer = setTimeout(() => {
    scrollTimer = null;
    nextTick(() => {
      if (containerRef.value) {
        containerRef.value.scrollTop = containerRef.value.scrollHeight;
      }
    });
  }, 50);
}

watch(() => props.messages.length, scrollToBottom);
watch(() => props.streamingContent, scrollToBottom);
watch(() => props.streamingThinking, scrollToBottom);
watch(() => props.streamingReferences?.length, scrollToBottom);

defineExpose({ scrollToBottom });
</script>

<template>
  <div ref="containerRef" class="chat-messages">
    <div class="message-list">
      <div
        v-for="msg in displayMessages"
        :key="msg.key"
        class="message-item"
        :class="msg.role"
      >
        <!-- 头像 -->
        <Avatar :size="36" class="message-avatar">
          <template #icon>
            <UserOutlined v-if="msg.role === 'user'" />
            <RobotOutlined v-else />
          </template>
        </Avatar>

        <!-- 消息内容 -->
        <div class="message-content">
          <!-- 用户消息 -->
          <div v-if="msg.role === 'user'" class="message-bubble user">
            {{ msg.content }}
          </div>

          <!-- AI 消息 -->
          <template v-else>
            <details v-if="msg.thinking" class="thinking-panel" :open="isStreaming">
              <summary>思考</summary>
              <div class="thinking-content">{{ msg.thinking }}</div>
            </details>
            <div class="message-bubble ai">
              <MarkdownRender custom-id="ai-chat" :content="msg.content" />
            </div>
            <div v-if="msg.references?.length" class="reference-panel">
              <div class="reference-title">引用来源</div>
              <div class="reference-list">
                <a
                  v-for="(reference, index) in msg.references"
                  :key="reference.url || reference.chunkId || `${reference.title}-${index}`"
                  class="reference-item"
                  :href="reference.url || undefined"
                  :target="reference.url ? '_blank' : undefined"
                  :rel="reference.url ? 'noreferrer' : undefined"
                >
                  <span class="reference-index">
                    {{ referenceDisplayIndex(reference, index) }}
                  </span>
                  <GlobalOutlined v-if="reference.type === 'web'" class="reference-icon" />
                  <FileTextOutlined v-else class="reference-icon" />
                  <span class="reference-body">
                    <span class="reference-name">
                      {{ reference.title || reference.documentName || '引用来源' }}
                    </span>
                    <span v-if="reference.siteName || reference.documentName || reference.chunkId" class="reference-meta">
                      {{ reference.siteName || reference.documentName || `片段 ${reference.chunkId}` }}
                    </span>
                    <span v-if="reference.snippet" class="reference-snippet">
                      {{ reference.snippet }}
                    </span>
                  </span>
                  <LinkOutlined v-if="reference.url" class="reference-link-icon" />
                </a>
              </div>
            </div>
            <!-- 操作按钮 -->
            <div class="message-actions">
              <Button type="text" size="small" @click="handleCopy(msg.content)">
                <CopyOutlined /> 复制
              </Button>
              <Button type="text" size="small" @click="handleRetry">
                <RedoOutlined /> 重试
              </Button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.chat-messages {
  flex: 1;
  min-height: 0;
  padding: 24px 0;
  overflow: hidden scroll;
  // 始终显示滚动条轨道，防止内容变化导致的抖动
  &::-webkit-scrollbar {
    width: 8px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    background: rgb(0 0 0 / 15%);
    border-radius: 4px;

    &:hover {
      background: rgb(0 0 0 / 25%);
    }
  }
}

// ==================== 消息列表 ====================
.message-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 0 24px;
}

// ==================== 消息项 ====================
.message-item {
  display: flex;
  gap: 12px;
  max-width: 80%;

  // 用户消息 - 靠右
  &.user {
    flex-direction: row-reverse;
    align-self: flex-end;
  }

  // AI 消息 - 靠左
  &.ai {
    align-self: flex-start;
  }
}

// ==================== 头像 ====================
.message-avatar {
  flex-shrink: 0;

  .message-item.user & {
    background: #1890ff;
  }

  .message-item.ai & {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  }
}

// ==================== 消息内容 ====================
.message-content {
  min-width: 0;
}

// ==================== 思考过程 ====================
.thinking-panel {
  max-width: 100%;
  padding: 10px 14px;
  margin-bottom: 8px;
  color: var(--text-color-secondary, #666);
  background: #fafafa;
  border: 1px solid var(--border-color, #eee);
  border-radius: 8px;

  summary {
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    user-select: none;
  }

  .thinking-content {
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.7;
    white-space: pre-wrap;
  }
}

// ==================== 消息气泡 ====================
.message-bubble {
  padding: 14px 18px;
  word-break: break-word;
  border-radius: 18px;

  &.user {
    color: #fff;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-radius: 18px 4px 18px 18px;
  }

  &.ai {
    background: var(--hover-color, #f7f7f8);
    border-radius: 4px 18px 18px;
  }
}

// ==================== 操作按钮 ====================
.message-actions {
  margin-top: 8px;
  opacity: 0.5;
  transition: opacity 0.2s;

  &:hover {
    opacity: 1;
  }

  :deep(.ant-btn) {
    padding: 0 8px;
    color: var(--text-color-secondary, #999);

    .anticon {
      margin-right: 4px;
    }
  }
}

// ==================== 引用来源 ====================
.reference-panel {
  max-width: 100%;
  margin-top: 8px;
}

.reference-title {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary, #666);
}

.reference-list {
  display: grid;
  gap: 6px;
}

.reference-item {
  display: grid;
  grid-template-columns: 22px 18px minmax(0, 1fr) 16px;
  gap: 8px;
  align-items: flex-start;
  max-width: 100%;
  padding: 8px 10px;
  color: var(--text-color, #333);
  text-decoration: none;
  background: #fff;
  border: 1px solid var(--border-color, #e8e8e8);
  border-radius: 8px;

  &:hover {
    color: var(--primary-color, #1890ff);
    border-color: var(--primary-color, #1890ff);
  }
}

.reference-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 12px;
  color: #666;
  background: #f5f5f5;
  border-radius: 50%;
}

.reference-icon,
.reference-link-icon {
  margin-top: 2px;
  color: var(--text-color-secondary, #999);
}

.reference-body {
  display: grid;
  min-width: 0;
}

.reference-name,
.reference-meta,
.reference-snippet {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-name {
  font-size: 13px;
  font-weight: 500;
}

.reference-meta,
.reference-snippet {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-color-secondary, #777);
}

// ==================== Markdown 样式 ====================
:deep(.markstream-vue) {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-color, #333);
  background: transparent !important;

  p {
    margin: 0 0 12px;

    &:last-child {
      margin-bottom: 0;
    }
  }

  // 代码块容器
  .code-block-wrapper {
    margin: 16px 0;
    overflow: hidden;
    border: 1px solid var(--border-color, #e8e8e8);
    border-radius: 8px;
  }

  // 代码块样式
  // pre {
  //   margin: 0;
  //   padding: 16px;
  //   overflow-x: auto;
  //   background: #282c34 !important;

  //   code {
  //     background: transparent !important;
  //     color: #abb2bf;
  //     font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Consolas', monospace;
  //     font-size: 13px;
  //     line-height: 1.7;
  //   }
  // }

  code {
    font-family: 'JetBrains Mono', 'Fira Code', Monaco, Consolas, monospace;
    font-size: 13px;
  }

  // 行内代码
  :not(pre) > code {
    padding: 3px 6px;
    font-size: 0.875em;
    color: #e83e8c;
    background: rgb(27 31 35 / 5%);
    border: 1px solid rgb(27 31 35 / 10%);
    border-radius: 4px;
  }

  table {
    width: 100%;
    margin: 16px 0;
    border-collapse: collapse;

    th,
    td {
      padding: 10px 14px;
      text-align: left;
      border: 1px solid var(--border-color, #e8e8e8);
    }

    th {
      font-weight: 600;
      background: var(--hover-color, #fafafa);
    }
  }

  blockquote {
    padding: 12px 20px;
    margin: 16px 0;
    color: var(--text-color-secondary, #666);
    background: var(--hover-color, #f9f9f9);
    border-left: 4px solid var(--primary-color, #1890ff);
    border-radius: 0 8px 8px 0;
  }

  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    margin-top: 20px;
    margin-bottom: 12px;
    font-weight: 600;
    line-height: 1.4;

    &:first-child {
      margin-top: 0;
    }
  }

  h1 {
    font-size: 1.5em;
  }

  h2 {
    font-size: 1.3em;
  }

  h3 {
    font-size: 1.15em;
  }

  ul,
  ol {
    padding-left: 24px;
    margin: 12px 0;

    li {
      margin: 6px 0;
      line-height: 1.6;
    }
  }

  hr {
    margin: 20px 0;
    border: none;
    border-top: 1px solid var(--border-color, #e8e8e8);
  }

  a {
    color: var(--primary-color, #1890ff);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  strong {
    font-weight: 600;
  }

  img {
    max-width: 100%;
    border-radius: 8px;
  }
}

@media (max-width: 640px) {
  .chat-messages {
    padding: 16px 0;
  }

  .message-list {
    gap: 16px;
    padding: 0 14px;
  }

  .message-item {
    max-width: 100%;

    &.user {
      max-width: 88%;
    }
  }

  .message-content {
    max-width: calc(100vw - 76px);
  }

  .message-bubble {
    padding: 12px 14px;
    border-radius: 12px;

    &.user {
      border-radius: 12px 4px 12px 12px;
    }

    &.ai {
      border-radius: 4px 12px 12px;
    }
  }

  :deep(.markstream-vue) {
    font-size: 14px;

    table {
      table-layout: fixed;
    }

    th,
    td {
      padding: 8px;
      overflow-wrap: anywhere;
      word-break: normal;
    }

    th:first-child,
    td:first-child {
      width: 34%;
    }
  }
}
</style>
