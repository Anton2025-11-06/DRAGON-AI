<script lang="ts" setup>
import type { ChatMessage } from '#/views/wemirr/ai/chat/components';

import { ref, watch } from 'vue';

import { VbenIcon } from '@vben/icons';

import { message } from 'ant-design-vue';

import { useIamAgentStore } from '#/store';
import {
  ConversationType,
  createConversation,
  getChatStreamUrl,
  getConversationMessages,
  getConversationPage,
  getModelList,
} from '#/views/wemirr/ai/chat/api';
import { ChatInput, ChatMessages } from '#/views/wemirr/ai/chat/components';
import { toChatMessage, useSseStream } from '#/views/wemirr/ai/shared';

const store = useIamAgentStore();
const messages = ref<ChatMessage[]>([]);
const conversationId = ref<null | string>(null);
const inputValue = ref('');
const selectedModelId = ref<number>();
const deepThinking = ref(true);
const webSearch = ref(false);
const initializing = ref(false);
const initialized = ref(false);

const {
  isStreaming,
  startStream,
  stopStream,
  streamingContent,
  streamingReferences,
  streamingThinking,
} = useSseStream();

watch(
  () => store.drawerOpen,
  (open) => {
    if (open && !initialized.value) void initializeChat();
  },
  { immediate: true },
);

async function initializeChat() {
  if (initializing.value) return;
  initializing.value = true;
  try {
    const [models, conversations] = await Promise.all([
      getModelList(),
      getConversationPage({
        current: 1,
        size: 20,
        type: ConversationType.PLATFORM_AGENT,
      }),
    ]);
    selectedModelId.value = models[0]?.id;
    const latest = conversations.records?.[0];
    if (latest) {
      conversationId.value = latest.id;
      const history = await getConversationMessages(latest.id);
      messages.value = history.map(
        (item) => toChatMessage(item) as ChatMessage,
      );
    }
    initialized.value = true;
  } catch (error) {
    console.error('初始化平台助手失败', error);
    message.error('平台助手暂时无法连接，请稍后重试');
  } finally {
    initializing.value = false;
  }
}

async function ensureConversation(content: string) {
  if (conversationId.value) return conversationId.value;
  const title = `${content.slice(0, 20)}${content.length > 20 ? '...' : ''}`;
  const conversation = await createConversation({
    title,
    type: ConversationType.PLATFORM_AGENT,
  });
  conversationId.value = conversation.id;
  return conversation.id;
}

async function handleSend(value: string) {
  const content = value.trim();
  if (!content || isStreaming.value) return;
  if (!selectedModelId.value) {
    message.error('当前没有可用的对话模型');
    return;
  }

  let activeConversationId: string;
  try {
    activeConversationId = await ensureConversation(content);
  } catch (error) {
    console.error('创建平台助手会话失败', error);
    message.error('创建对话失败，请稍后重试');
    return;
  }

  messages.value.push({
    content,
    key: `user-${Date.now()}`,
    role: 'user',
  });
  messages.value.push({
    content: '',
    key: `assistant-${Date.now()}`,
    role: 'ai',
  });
  const assistantIndex = messages.value.length - 1;
  inputValue.value = '';

  try {
    const result = await startStream({
      url: getChatStreamUrl(),
      payload: {
        chatType: ConversationType.PLATFORM_AGENT,
        conversationId: activeConversationId,
        enableWebSearch: webSearch.value,
        modelId: selectedModelId.value,
        prompt: content,
        returnThinking: deepThinking.value,
      },
    });
    const assistantMessage = messages.value[assistantIndex];
    if (assistantMessage) {
      assistantMessage.content = result.content || '我暂时没有生成有效回复，请再试一次。';
      assistantMessage.references = result.references;
      assistantMessage.thinking = result.thinking || undefined;
    }
  } catch (error) {
    const assistantMessage = messages.value[assistantIndex];
    if (assistantMessage) {
      assistantMessage.content = error instanceof Error && error.message
        ? error.message
        : '抱歉，这次回复没有完成，请稍后重试。';
    }
  }
}

function handleStop() {
  stopStream();
}

function handleRetry(content: string) {
  void handleSend(content);
}

function startNewConversation() {
  if (isStreaming.value) {
    message.warning('请先停止当前回复');
    return;
  }
  conversationId.value = null;
  messages.value = [];
  inputValue.value = '';
}

defineExpose({ handleSend, messages, store });
</script>

<template>
  <a-drawer
    :body-style="{ padding: 0, overflow: 'hidden' }"
    :open="store.drawerOpen"
    title="AI 助手"
    width="min(720px, 100vw)"
    @close="store.closeDrawer"
  >
    <template #extra>
      <a-tooltip title="新对话">
        <a-button
          aria-label="新对话"
          :disabled="isStreaming"
          shape="circle"
          type="text"
          @click="startNewConversation"
        >
          <VbenIcon class="size-4" icon="lucide:square-pen" />
        </a-button>
      </a-tooltip>
    </template>

    <div data-testid="iam-agent-content" class="assistant-shell">
      <div v-if="messages.length === 0 && !initializing" class="assistant-welcome">
        <span class="assistant-mark">
          <VbenIcon class="size-7" icon="lucide:sparkles" />
        </span>
        <h2>今天想聊点什么？</h2>
      </div>

      <a-spin v-if="initializing" class="assistant-loading" />

      <ChatMessages
        v-else
        data-testid="iam-chat-messages"
        :is-streaming="isStreaming"
        :messages="messages"
        :streaming-content="streamingContent"
        :streaming-references="streamingReferences"
        :streaming-thinking="streamingThinking"
        @retry="handleRetry"
      />

      <ChatInput
        v-model:value="inputValue"
        v-model:deep-thinking="deepThinking"
        v-model:web-search="webSearch"
        :loading="isStreaming"
        :show-model-select="false"
        @cancel="handleStop"
        @submit="handleSend"
      />
    </div>
  </a-drawer>
</template>

<style lang="less" scoped>
.assistant-shell {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
}

.assistant-welcome {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: 32px 20px 12vh;
  color: var(--text-color, #262626);

  h2 {
    margin: 18px 0 0;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: 0;
  }
}

.assistant-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  color: #fff;
  background: #111827;
  border-radius: 8px;
}

.assistant-loading {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
}

@media (max-width: 640px) {
  .assistant-welcome h2 {
    font-size: 21px;
  }
}
</style>
