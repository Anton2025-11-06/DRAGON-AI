<script setup lang="ts" name="RagChatPage">
import type { ConversationMessageRep, ModelConfig } from '../api';
import type { ChatMessage } from '../components';
import type { KnowledgeBase, RagAskReq } from './api';

import { onMounted, ref, watch } from 'vue';

import { useAccessStore } from '@vben/stores';

import { message } from 'ant-design-vue';
import { SSE } from 'sse.js';

import { resolveApiUrl } from '#/api/helper';

import { ConversationType, getModelList } from '../api';
import { ChatInput, ChatMessages } from '../components';
import {
  applySseMessage,
  createStreamState,
  toChatMessage,
  type ChatReference,
} from '../../shared';
import { getMessagesByKbId, getRagChatStreamUrl } from './api';
import KnowledgeBaseList from './KnowledgeBaseList.vue';
import RagWelcome from './RagWelcome.vue';

// ==================== 状态管理 ====================

// 知识库相关
const selectedKnowledgeBase = ref<KnowledgeBase | null>(null);

// 消息相关
const messages = ref<ChatMessage[]>([]);
const messagesLoading = ref(false);

// 输入相关
const inputValue = ref('');
const isStreaming = ref(false);

// 模型相关
const models = ref<ModelConfig[]>([]);
const selectedModelId = ref<number | undefined>(undefined);

// 选项相关
const enableDeepThinking = ref(false);
const enableWebSearch = ref(false);

// SSE 实例
let sseSource: null | SSE = null;

// 流式输出
const streamingContent = ref('');
const streamingThinking = ref('');
const streamingReferences = ref<ChatReference[]>([]);

// ==================== 生命周期 ====================

onMounted(async () => {
  await loadModels();
});

// 监听知识库切换，加载该知识库的历史消息
watch(selectedKnowledgeBase, async (newKb) => {
  if (newKb) {
    await loadMessages(newKb.id);
  } else {
    messages.value = [];
  }
});

// ==================== 数据加载 ====================

async function loadModels() {
  try {
    models.value = await getModelList();
    if (models.value.length > 0 && !selectedModelId.value) {
      selectedModelId.value = models.value[0]?.id;
    }
  } catch {
    console.error('加载模型列表失败');
  }
}

async function loadMessages(kbId: number) {
  try {
    messagesLoading.value = true;
    const data = await getMessagesByKbId(kbId);
    // 后端返回字段映射：role 大写，displayContent/thinkingContent
    messages.value = data.map(
      (msg: ConversationMessageRep) => toChatMessage(msg),
    ) as ChatMessage[];
  } catch {
    console.error('加载消息列表失败');
    messages.value = [];
  } finally {
    messagesLoading.value = false;
  }
}

// ==================== 知识库选择 ====================

function handleKnowledgeBaseSelect(kb: KnowledgeBase) {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  selectedKnowledgeBase.value = kb;
}

// ==================== SSE 流式请求 ====================

async function sseStreamResponse(prompt: string) {
  return new Promise<void>((resolve, reject) => {
    const accessStore = useAccessStore();
    const token = accessStore.accessToken;
    const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';

    if (!selectedKnowledgeBase.value) {
      message.warning('请先选择知识库');
      resolve();
      return;
    }

    const payload: RagAskReq = {
      chatType: ConversationType.KNOWLEDGE_BASE,
      kbId: selectedKnowledgeBase.value.id,
      prompt,
      returnThinking: enableDeepThinking.value,
      enableWebSearch: enableWebSearch.value,
    };

    sseSource = new SSE(resolveApiUrl(getRagChatStreamUrl(), baseUrl), {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(payload),
      method: 'POST',
    });
    const streamState = createStreamState();
    let settled = false;

    const finish = () => {
      if (settled) return;
      settled = true;
      sseSource = null;
      resolve();
    };

    const fail = (detail: string) => {
      if (settled) return;
      settled = true;
      const source = sseSource;
      sseSource = null;
      reject(new Error(detail || 'RAG 对话请求失败'));
      source?.close();
    };

    sseSource.addEventListener('message', (event: MessageEvent) => {
      const rawData = event.data;
      if (!rawData) return;

      applySseMessage(rawData, streamState);
      streamingContent.value = streamState.content;
      streamingThinking.value = streamState.thinking;
      streamingReferences.value = [...streamState.references];
    });

    sseSource.addEventListener('error', (event: MessageEvent) => {
      const rawDetail = typeof event?.data === 'string' ? event.data : '';
      let detail = rawDetail;
      try {
        detail = JSON.parse(rawDetail).message || rawDetail;
      } catch {
        // 后端兼容纯文本错误事件。
      }
      fail(detail);
    });

    sseSource.addEventListener('readystatechange', (e: any) => {
      if (e.readyState === 2) {
        finish();
      }
    });

    sseSource.stream();
  });
}

// ==================== 消息处理 ====================

async function handleSend(value: string) {
  const content = value.trim();
  if (!content || isStreaming.value) return;

  if (!selectedKnowledgeBase.value) {
    message.warning('请先选择知识库');
    return;
  }

  // 添加用户消息
  const userMsg: ChatMessage = {
    key: `user-${Date.now()}`,
    role: 'user',
    content,
  };
  messages.value.push(userMsg);

  inputValue.value = '';

  // 准备 AI 响应
  streamingContent.value = '';
  streamingThinking.value = '';
  streamingReferences.value = [];
  isStreaming.value = true;

  // 添加 AI 消息占位
  const aiMsg: ChatMessage = {
    key: `ai-${Date.now()}`,
    role: 'ai',
    content: '',
    thinking: '',
  };
  messages.value.push(aiMsg);
  const aiMsgIndex = messages.value.length - 1;

  let streamError: Error | null = null;
  try {
    await sseStreamResponse(content);
  } catch (error) {
    console.error('流式响应错误:', error);
    streamError = error instanceof Error ? error : new Error('请求失败，请重试');
    message.error(streamError.message);
  }

  // 流式结束，同步内容
  if (messages.value[aiMsgIndex]) {
    messages.value[aiMsgIndex]!.content = streamError
      ? `请求失败：${streamError.message}`
      : streamingContent.value;
    messages.value[aiMsgIndex]!.thinking = streamingThinking.value;
    messages.value[aiMsgIndex]!.references = streamingReferences.value;
  }
  isStreaming.value = false;
}

function handleStop() {
  isStreaming.value = false;
  if (sseSource) {
    sseSource.close();
    sseSource = null;
  }
}

function handleRetry(content: string) {
  handleSend(content);
}

function handleWelcomeSend(prompt: string) {
  handleSend(prompt);
}
</script>

<template>
  <div class="rag-chat-wrapper">
    <div class="rag-chat-page">
      <!-- 左侧知识库列表 -->
      <KnowledgeBaseList @select="handleKnowledgeBaseSelect" />

      <!-- 右侧聊天区域 -->
      <main class="chat-main">
        <!-- 聊天内容 -->
        <div class="chat-content">
          <!-- 欢迎页面（无消息时显示） -->
          <RagWelcome
            v-if="messages.length === 0 && !messagesLoading"
            :knowledge-base="selectedKnowledgeBase"
            @send="handleWelcomeSend"
          />

          <!-- 消息列表 -->
          <ChatMessages
            v-else
            :messages="messages"
            :streaming-content="streamingContent"
            :streaming-thinking="streamingThinking"
            :streaming-references="streamingReferences"
            :is-streaming="isStreaming"
            @retry="handleRetry"
          />
        </div>

        <!-- 输入区域 -->
        <ChatInput
          v-model:value="inputValue"
          v-model:model-id="selectedModelId"
          v-model:deep-thinking="enableDeepThinking"
          v-model:web-search="enableWebSearch"
          :models="models"
          :loading="isStreaming"
          @submit="handleSend"
          @cancel="handleStop"
        />
      </main>
    </div>
  </div>
</template>

<style lang="less" scoped>
.rag-chat-wrapper {
  height: calc(100vh - 140px);
  padding: 10px;
  background: var(--background-color, #f5f7f9);
}

.rag-chat-page {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
  border-radius: 8px;
  box-shadow: 0 1px 2px rgb(0 0 0 / 3%);
}

.chat-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
}

.chat-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
</style>
