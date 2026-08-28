<script setup lang="ts" name="AiChatPage">
import type {
  AskReq,
  ConversationMessageRep,
  ConversationPageRep,
  ModelConfig,
} from './api';
import type { ChatMessage } from './components';

import { computed, onMounted, ref, watch } from 'vue';

import { useAccessStore } from '@vben/stores';

import { MenuOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { Button, Drawer, message } from 'ant-design-vue';
import { SSE } from 'sse.js';

import { resolveApiUrl } from '#/api/helper';

import {
  createConversation as apiCreateConversation,
  deleteConversation as apiDeleteConversation,
  updateConversation as apiUpdateConversation,
  ConversationType,
  getChatStreamUrl,
  getConversationMessages,
  getConversationPage,
  getModelList,
} from './api';
import {
  applySseMessage,
  createStreamState,
  toChatMessage,
  type ChatReference,
} from '../shared';
import {
  ChatInput,
  ChatMessages,
  ChatSidebar,
  ChatWelcome,
} from './components';

// ==================== 类型定义 ====================

interface ConversationItem {
  key: string;
  label: string;
}

interface ConversationGroup {
  title: string;
  items: ConversationItem[];
}

// ==================== 状态管理 ====================

// 会话相关
const conversations = ref<ConversationPageRep[]>([]);
const activeConversationKey = ref<string>('');
const conversationsLoading = ref(false);
const conversationsTotal = ref(0);
const conversationsPage = ref(1);
const conversationsHasMore = ref(false);
const mobileSidebarOpen = ref(false);
const PAGE_SIZE = 20;

// 新建对话模式（点击新建但还未创建会话）
const isNewChatMode = ref(false);
const pendingMessages = ref<ChatMessage[]>([]);

// 消息相关
const messagesMap = ref<Record<string, ChatMessage[]>>({});
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

// ==================== 计算属性 ====================

// 当前会话的消息（新建模式用 pendingMessages）
const currentMessages = computed(() => {
  if (isNewChatMode.value) {
    return pendingMessages.value;
  }
  return messagesMap.value[activeConversationKey.value] || [];
});

// 按日期分组会话列表（每组内按创建时间倒序）
const groupedConversations = computed<ConversationGroup[]>(() => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);

  const groups: Record<string, { item: ConversationItem; time: number }[]> = {
    今天: [],
    昨天: [],
    近七天: [],
    更早: [],
  };

  conversations.value.forEach((conv) => {
    const date = new Date(conv.createTime);
    const entry = {
      item: { key: conv.id, label: conv.title || '新对话' },
      time: date.getTime(),
    };

    if (date >= today) {
      groups['今天']!.push(entry);
    } else if (date >= yesterday) {
      groups['昨天']!.push(entry);
    } else if (date >= weekAgo) {
      groups['近七天']!.push(entry);
    } else {
      groups['更早']!.push(entry);
    }
  });

  // 每组内按时间倒序排列
  return Object.entries(groups)
    .filter(([, entries]) => entries && entries.length > 0)
    .map(([title, entries]) => ({
      title,
      items: entries!.toSorted((a, b) => b.time - a.time).map((e) => e.item),
    }));
});

// ==================== 生命周期 ====================

onMounted(async () => {
  await Promise.all([loadModels(), loadConversations()]);
});

// 监听会话切换 - 只在消息未缓存时加载
watch(activeConversationKey, async (newKey) => {
  if (newKey && !isNewChatMode.value && !messagesMap.value[newKey]) {
    console.log('[DEBUG] watch 触发，加载消息:', newKey);
    await loadConversationMessages(newKey);
  }
}, { flush: 'post' });

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

async function loadConversations(append = false) {
  try {
    conversationsLoading.value = true;
    const page = append ? conversationsPage.value + 1 : 1;
    const res = await getConversationPage({
      current: page,
      size: PAGE_SIZE,
      types: [ConversationType.NORMAL, ConversationType.PLATFORM_AGENT],
    });

    conversations.value = append
      ? [...conversations.value, ...(res.records || [])]
      : res.records || [];

    conversationsPage.value = page;
    conversationsTotal.value = res.total || 0;
    conversationsHasMore.value =
      conversations.value.length < conversationsTotal.value;

    // 只有在没有选中会话时才自动选中第一个
    if (conversations.value.length > 0 && !activeConversationKey.value) {
      activeConversationKey.value = conversations.value[0]!.id;
    }
  } catch {
    console.error('加载会话列表失败');
  } finally {
    conversationsLoading.value = false;
  }
}

/** 加载更多会话 */
async function handleLoadMore() {
  if (!conversationsHasMore.value || conversationsLoading.value) return;
  await loadConversations(true);
}

async function loadConversationMessages(conversationId: string) {
  if (messagesMap.value[conversationId]) {
    console.log('[DEBUG] 消息已缓存，跳过加载:', conversationId);
    return;
  }

  try {
    messagesLoading.value = true;
    console.log('[DEBUG] 开始加载消息:', conversationId);
    const messages = await getConversationMessages(conversationId);
    // 后端返回字段映射：role 大写，displayContent/thinkingContent
    messagesMap.value[conversationId] = messages.map(
      (msg: ConversationMessageRep) => toChatMessage(msg),
    ) as ChatMessage[];
    console.log('[DEBUG] 消息加载完成:', conversationId, '数量:', messagesMap.value[conversationId].length);
  } catch {
    console.error('加载消息列表失败');
    messagesMap.value[conversationId] = [];
  } finally {
    messagesLoading.value = false;
  }
}

// ==================== 会话管理 ====================

/** 新建对话 - 进入新建模式，不调用接口 */
function handleCreateConversation() {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  isNewChatMode.value = true;
  activeConversationKey.value = '';
  pendingMessages.value = [];
}

function handleConversationChange(key: string) {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  isNewChatMode.value = false;
  activeConversationKey.value = key;
}

function handleMobileConversationChange(key: string) {
  mobileSidebarOpen.value = false;
  handleConversationChange(key);
}

function handleMobileCreateConversation() {
  mobileSidebarOpen.value = false;
  handleCreateConversation();
}

async function handleDeleteConversation(key: string) {
  try {
    await apiDeleteConversation(key);
    delete messagesMap.value[key];
    await loadConversations();
    if (activeConversationKey.value === key) {
      activeConversationKey.value = conversations.value[0]?.id || '';
    }
  } catch (error: any) {
    const errorMsg =
      error?.response?.data?.message ||
      error?.message ||
      '服务异常，请稍后再试';
    message.error(errorMsg);
  }
}

function handleRenameConversation(key: string, name: string) {
  const conv = conversations.value.find((c) => c.id === key);
  if (conv) {
    // 先更新本地状态
    const oldTitle = conv.title;
    conv.title = name;
    // 调用接口更新
    apiUpdateConversation(key, {
      title: name,
      type: ConversationType.PLATFORM_AGENT,
    }).catch((error: any) => {
      // 失败时回滚
      conv.title = oldTitle;
      const errorMsg =
        error?.response?.data?.message || error?.message || '重命名失败';
      message.error(errorMsg);
    });
  }
}

// ==================== SSE 流式请求 ====================

async function sseStreamResponse(prompt: string, conversationId?: string) {
  const accessStore = useAccessStore();
  const token = accessStore.accessToken;
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';

  const payload: AskReq = {
    chatType: ConversationType.PLATFORM_AGENT,
    modelId: selectedModelId.value,
    prompt,
    returnThinking: enableDeepThinking.value,
    enableWebSearch: enableWebSearch.value,
    conversationId: conversationId || activeConversationKey.value || undefined,
  };

  const url = resolveApiUrl(getChatStreamUrl(), baseUrl);

  return new Promise<void>((resolve, reject) => {
    sseSource = new SSE(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(payload),
      method: 'POST',
    });

    // 标记是否已处理错误
    let errorHandled = false;
    const streamState = createStreamState();

    /** 解析并处理错误响应 */
    function handleErrorResponse(responseText: string): boolean {
      if (!responseText || errorHandled) return false;
      try {
        const errorData = JSON.parse(responseText);
        if (errorData.successful === false && errorData.message) {
          errorHandled = true;
          sseSource?.close();
          sseSource = null;
          reject(new Error(errorData.message));
          return true;
        }
      } catch {
        // 非 JSON 格式，忽略
      }
      return false;
    }

    sseSource.addEventListener('message', (event: MessageEvent) => {
      const rawData = event.data;
      if (!rawData || errorHandled) return;

      // 先检查是否为错误响应
      if (handleErrorResponse(rawData)) return;

      applySseMessage(rawData, streamState);
      streamingContent.value = streamState.content;
      streamingThinking.value = streamState.thinking;
      streamingReferences.value = [...streamState.references];
    });

    sseSource.addEventListener('error', (e: any) => {
      if (errorHandled) return;
      const xhr = (e as any).source?.xhr || (e as any).target;
      if (xhr?.responseText && handleErrorResponse(xhr.responseText)) return;
      sseSource?.close();
      sseSource = null;
      resolve();
    });

    sseSource.addEventListener('readystatechange', (e: any) => {
      const xhr = e.target;
      if (errorHandled) return;

      if (xhr?.readyState === 4) {
        if (xhr.responseText && handleErrorResponse(xhr.responseText)) return;
        sseSource = null;
        resolve();
      } else if (e.readyState === 2) {
        sseSource = null;
        resolve();
      }
    });

    sseSource.addEventListener('abort', () => {
      if (!errorHandled) {
        sseSource = null;
        resolve();
      }
    });

    sseSource.stream();
  });
}

// ==================== 消息处理 ====================

async function handleSend(value: string) {
  const content = value.trim();
  if (!content || isStreaming.value) return;

  // 新建模式或没有选中会话：先显示消息，创建会话后发起请求
  if (isNewChatMode.value || !activeConversationKey.value) {
    // 添加用户消息到 pendingMessages
    const userMsg: ChatMessage = {
      key: `user-${Date.now()}`,
      role: 'user',
      content,
    };
    pendingMessages.value.push(userMsg);

    // 添加 AI 消息占位
    const aiMsg: ChatMessage = {
      key: `ai-${Date.now()}`,
      role: 'ai',
      content: '',
      thinking: '',
    };
    pendingMessages.value.push(aiMsg);
    const aiMsgIndex = pendingMessages.value.length - 1;

    inputValue.value = '';
    streamingContent.value = '';
    streamingThinking.value = '';
    streamingReferences.value = [];
    isStreaming.value = true;
    isNewChatMode.value = true; // 确保进入新建模式以显示 pendingMessages

    // 创建会话（标题用输入内容前20字）
    let convId: string;
    try {
      const title = content.slice(0, 20) + (content.length > 20 ? '...' : '');
      const result = await apiCreateConversation({
        title,
        type: ConversationType.PLATFORM_AGENT,
      });
      convId = result.id;
      console.log('[DEBUG] 创建会话成功，ID:', convId);

      if (!convId) {
        throw new Error('创建会话失败：返回的 ID 为空');
      }
    } catch (error: any) {
      const errorMsg = error?.response?.data?.message || error?.message || '创建会话失败';
      message.error(errorMsg);
      isStreaming.value = false;
      isNewChatMode.value = false;
      pendingMessages.value = [];
      return;
    }

    // 发起 SSE 请求
    try {
      await sseStreamResponse(content, convId);
    } catch (error: any) {
      console.error('流式响应错误:', error);
      streamingContent.value = `❌ ${error?.message || '请求失败，请重试'}`;
    }

    // 流式结束，同步内容到 pendingMessages
    if (pendingMessages.value[aiMsgIndex]) {
      pendingMessages.value[aiMsgIndex]!.content = streamingContent.value;
      pendingMessages.value[aiMsgIndex]!.thinking = streamingThinking.value;
      pendingMessages.value[aiMsgIndex]!.references = streamingReferences.value;
    }

    // 关键：先转移消息到 messagesMap，再切换会话
    const messagesToSave = [...pendingMessages.value];
    console.log('[DEBUG] 转移消息到 messagesMap[' + convId + ']，数量:', messagesToSave.length);
    messagesMap.value[convId] = messagesToSave;
    console.log('[DEBUG] 转移完成，messagesMap[' + convId + ']:', messagesMap.value[convId]?.length ?? 0);

    // 然后切换会话（这会触发 watch，但此时 messagesMap[convId] 已有数据）
    console.log('[DEBUG] 切换 activeConversationKey 到:', convId);
    activeConversationKey.value = convId;

    // 最后退出新建模式
    isNewChatMode.value = false;

    // 清空临时状态
    pendingMessages.value = [];
    streamingContent.value = '';
    streamingThinking.value = '';
    streamingReferences.value = [];
    isStreaming.value = false;

    // 异步刷新会话列表
    setTimeout(() => {
      loadConversations();
    }, 100);
    return;
  }

  // 正常模式：已有会话
  const convKey = activeConversationKey.value;
  if (!messagesMap.value[convKey]) {
    messagesMap.value[convKey] = [];
  }

  // 添加用户消息
  const userMsg: ChatMessage = {
    key: `user-${Date.now()}`,
    role: 'user',
    content,
  };
  messagesMap.value[convKey]!.push(userMsg);

  inputValue.value = '';
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
  messagesMap.value[convKey]!.push(aiMsg);
  const aiMsgIndex = messagesMap.value[convKey]!.length - 1;

  try {
    await sseStreamResponse(content);
  } catch (error: any) {
    console.error('流式响应错误:', error);
    streamingContent.value = `❌ ${error?.message || '请求失败，请重试'}`;
  }

  // 流式结束，同步内容
  if (messagesMap.value[convKey]?.[aiMsgIndex]) {
    messagesMap.value[convKey]![aiMsgIndex]!.content = streamingContent.value;
    messagesMap.value[convKey]![aiMsgIndex]!.thinking = streamingThinking.value;
    messagesMap.value[convKey]![aiMsgIndex]!.references = streamingReferences.value;
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
  inputValue.value = prompt;
  handleSend(prompt);
}
</script>

<template>
  <div class="chat-wrapper">
    <header class="mobile-chat-toolbar">
      <Button
        type="text"
        class="mobile-toolbar-action"
        title="会话列表"
        aria-label="打开会话列表"
        @click="mobileSidebarOpen = true"
      >
        <MenuOutlined />
      </Button>
      <span class="mobile-chat-title">AI 助手</span>
      <Button
        type="text"
        class="mobile-toolbar-action"
        title="新建对话"
        aria-label="新建对话"
        @click="handleMobileCreateConversation"
      >
        <PlusOutlined />
      </Button>
    </header>

    <Drawer
      v-model:open="mobileSidebarOpen"
      title="会话"
      placement="left"
      :width="320"
    >
      <ChatSidebar
        class="drawer-chat-sidebar"
        :grouped-conversations="groupedConversations"
        :active-key="activeConversationKey || ''"
        :loading="conversationsLoading"
        :is-empty="conversations.length === 0"
        :total="conversationsTotal"
        :has-more="conversationsHasMore"
        @create="handleMobileCreateConversation"
        @change="handleMobileConversationChange"
        @delete="handleDeleteConversation"
        @rename="handleRenameConversation"
        @load-more="handleLoadMore"
      />
    </Drawer>

    <div class="chat-page">
      <!-- 左侧会话列表 -->
      <ChatSidebar
        class="desktop-chat-sidebar"
        :grouped-conversations="groupedConversations"
        :active-key="activeConversationKey || ''"
        :loading="conversationsLoading"
        :is-empty="conversations.length === 0"
        :total="conversationsTotal"
        :has-more="conversationsHasMore"
        @create="handleCreateConversation"
        @change="handleConversationChange"
        @delete="handleDeleteConversation"
        @rename="handleRenameConversation"
        @load-more="handleLoadMore"
      />

      <!-- 右侧聊天区域 -->
      <main class="chat-main">
        <!-- 聊天内容 -->
        <div class="chat-content">
        <!-- 欢迎页面 -->
          <ChatWelcome
            v-show="currentMessages.length === 0 && !messagesLoading"
            @send="handleWelcomeSend"
          />

          <!-- 消息列表 -->
          <ChatMessages
            v-show="currentMessages.length > 0 || messagesLoading"
            :messages="currentMessages"
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
.chat-wrapper {
  height: calc(100vh - 140px);
  padding: 10px;
  background: var(--background-color, #f5f7f9);
}

.mobile-chat-toolbar {
  display: none;
}

.drawer-chat-sidebar {
  width: 100%;
  min-width: 0;
  border: 0;
  border-radius: 0;
}

.chat-page {
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

@media (max-width: 768px) {
  .chat-wrapper {
    height: calc(100vh - 88px);
    padding: 0;
    background: var(--component-background, #fff);
  }

  .mobile-chat-toolbar {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr) 40px;
    align-items: center;
    height: 52px;
    padding: 0 8px;
    border-bottom: 1px solid var(--border-color, #e8e8e8);
  }

  .mobile-chat-title {
    overflow: hidden;
    font-size: 16px;
    font-weight: 600;
    text-align: center;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mobile-toolbar-action {
    width: 40px;
    height: 40px;
    padding: 0;
  }

  .chat-page {
    height: calc(100% - 52px);
    border-radius: 0;
    box-shadow: none;
  }

  .desktop-chat-sidebar {
    display: none;
  }

  .chat-main {
    width: 100%;
  }
}
</style>
