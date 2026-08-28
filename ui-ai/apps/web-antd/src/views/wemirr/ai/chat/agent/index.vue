<script setup lang="ts" name="AgentWorkbench">
/**
 * 智能体工作台
 * 融合式设计：智能体列表 + 会话管理 + 对话功能
 */
import type { ConversationMessageRep } from '../api';
import type { ChatMessage } from '../components';
import type { AgentDetail, Conversation } from './api';

import { computed, onMounted, ref, watch } from 'vue';

import { PlusOutlined } from '@ant-design/icons-vue';
import { Button, message, Modal } from 'ant-design-vue';

import { AiSidebar, toChatMessage, useSseStream } from '../../shared';
import { ConversationType } from '../api';
import { ChatInput, ChatMessages } from '../components';
import AgentWelcome from './AgentWelcome.vue';
import * as api from './api';
import ConversationList from './ConversationList.vue';

// ==================== 状态管理 ====================

// 智能体相关
const agents = ref<AgentDetail[]>([]);
const agentsLoading = ref(false);
const selectedAgentId = ref<null | number>(null);

// 会话相关
const conversations = ref<Conversation[]>([]);
const conversationsLoading = ref(false);
const selectedConversationId = ref<null | string>(null);

// 消息相关
const messages = ref<ChatMessage[]>([]);
const messagesLoading = ref(false);

// 输入相关
const inputValue = ref('');

// 选项相关
const enableDeepThinking = ref(false);
const enableWebSearch = ref(false);

// ==================== 计算属性 ====================

/** 当前选中的智能体 */
const selectedAgent = computed(() =>
  agents.value.find((a) => a.id === selectedAgentId.value),
);

/** 当前选中的会话 */
const selectedConversation = computed(() =>
  conversations.value.find((c) => c.id === selectedConversationId.value),
);

/** 侧边栏智能体列表数据 */
const agentSidebarItems = computed(() =>
  agents.value.map((agent) => ({
    key: agent.id,
    label: agent.name,
    description: agent.description,
    avatar: agent.avatar,
  })),
);

// ==================== SSE 流式请求 ====================

const {
  isStreaming,
  streamingContent,
  streamingThinking,
  streamingReferences,
  startStream,
  stopStream,
} = useSseStream();

// ==================== 生命周期 ====================

onMounted(async () => {
  await loadAgents();
});

// 监听智能体切换
watch(selectedAgentId, async (newId) => {
  // 清空会话和消息
  selectedConversationId.value = null;
  conversations.value = [];
  messages.value = [];

  if (newId) {
    await loadConversations(newId);
  }
});

// 监听会话切换 - 只在从历史会话切换时加载消息
watch(selectedConversationId, async (newId) => {
  // 如果是新建会话（刚创建的），不需要从后端加载
  // 因为消息已经在 handleSend 中添加到 messages 数组了
  if (newId && conversations.value.some((c) => c.id === newId)) {
    await loadMessages(newId);
  }
});

// ==================== 数据加载 ====================

/** 加载智能体列表 */
async function loadAgents() {
  agentsLoading.value = true;
  try {
    const res = await api.getAgentPage({ current: 1, size: 100 });
    agents.value = res.records || [];
    // 默认选中第一个
    if (agents.value.length > 0 && !selectedAgentId.value) {
      selectedAgentId.value = agents.value[0]!.id;
    }
  } catch {
    message.error('加载智能体列表失败');
  } finally {
    agentsLoading.value = false;
  }
}

/** 加载会话列表 */
async function loadConversations(agentId: number) {
  conversationsLoading.value = true;
  try {
    const res = await api.getConversationsByAgent(agentId);
    conversations.value = res.records || [];
  } catch {
    console.error('加载会话列表失败');
    conversations.value = [];
  } finally {
    conversationsLoading.value = false;
  }
}

/** 加载消息列表 */
async function loadMessages(conversationId: string) {
  messagesLoading.value = true;
  try {
    const data = await api.getConversationMessages(conversationId);
    // 后端返回字段映射：role 大写，displayContent/thinkingContent
    messages.value = data.map(
      (msg: ConversationMessageRep) => toChatMessage(msg),
    ) as ChatMessage[];
  } catch {
    console.error('加载消息失败');
    messages.value = [];
  } finally {
    messagesLoading.value = false;
  }
}

// ==================== 智能体操作 ====================

function handleAgentChange(key: number | string) {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  selectedAgentId.value = key as number;
}

// ==================== 会话操作 ====================

/** 创建新会话 - 只清空状态，不调用接口 */
function handleCreateConversation() {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  // 清空当前会话状态，进入欢迎页
  selectedConversationId.value = null;
  messages.value = [];
}

/** 选择会话 */
function handleConversationSelect(id: string) {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  selectedConversationId.value = id;
}

/** 删除会话 */
async function handleConversationDelete(id: string) {
  Modal.confirm({
    title: '确认删除',
    content: '删除后无法恢复，确定要删除吗？',
    okText: '删除',
    okType: 'danger',
    onOk: async () => {
      try {
        await api.deleteConversation(id);
        message.success('删除成功');
        // 刷新列表
        if (selectedAgentId.value) {
          await loadConversations(selectedAgentId.value);
        }
        // 如果删除的是当前选中的会话，清空选中
        if (selectedConversationId.value === id) {
          selectedConversationId.value = null;
          messages.value = [];
        }
      } catch {
        message.error('删除失败');
      }
    },
  });
}

/** 重命名会话 */
async function handleConversationRename(id: string, title: string) {
  try {
    await api.updateConversation(id, { title });
    // 刷新列表
    if (selectedAgentId.value) {
      await loadConversations(selectedAgentId.value);
    }
  } catch {
    message.error('重命名失败');
  }
}

// ==================== 消息处理 ====================

/** 发送消息 */
async function handleSend(content: string) {
  const text = content.trim();
  if (!text || isStreaming.value) return;

  if (!selectedAgentId.value) {
    message.warning('请先选择智能体');
    return;
  }

  // 如果没有选中会话，创建新会话（标题用输入内容前20字）
  let conversationId = selectedConversationId.value;
  if (!conversationId) {
    try {
      const title = text.slice(0, 20) + (text.length > 20 ? '...' : '');
      const result = await api.createConversation({
        title,
        agentId: selectedAgentId.value,
        type: ConversationType.AGENT,
      });
      conversationId = result.id;
      selectedConversationId.value = conversationId;
      // 刷新会话列表
      await loadConversations(selectedAgentId.value);
    } catch {
      message.error('创建会话失败');
      return;
    }
  }

  // 添加用户消息
  const userMsg: ChatMessage = {
    key: `user-${Date.now()}`,
    role: 'user',
    content: text,
  };
  messages.value.push(userMsg);
  inputValue.value = '';

  // 添加 AI 消息占位
  const aiMsg: ChatMessage = {
    key: `ai-${Date.now()}`,
    role: 'ai',
    content: '',
    thinking: '',
  };
  messages.value.push(aiMsg);
  const aiMsgIndex = messages.value.length - 1;

  // 发起 SSE 请求
  try {
    await startStream({
      url: '/api/workflow/chat/stream',
      payload: {
        chatType: ConversationType.AGENT,
        agentId: selectedAgentId.value,
        conversationId,
        prompt: text,
        returnThinking: enableDeepThinking.value,
        enableWebSearch: enableWebSearch.value,
      },
    });
  } catch (error: any) {
    console.error('流式响应错误:', error);
  }

  // 流式结束，同步内容
  if (messages.value[aiMsgIndex]) {
    messages.value[aiMsgIndex]!.content =
      streamingContent.value || '❌ 请求失败，请重试';
    messages.value[aiMsgIndex]!.thinking = streamingThinking.value;
    messages.value[aiMsgIndex]!.references = streamingReferences.value;
  }
}

/** 停止生成 */
function handleStop() {
  stopStream();
}

/** 重试 */
function handleRetry(content: string) {
  handleSend(content);
}

/** 欢迎页快速发送 */
function handleWelcomeSend(prompt: string) {
  handleSend(prompt);
}
</script>

<template>
  <div class="agent-workbench">
    <!-- 左侧：智能体列表 -->
    <AiSidebar
      mode="list"
      logo-text="智能体"
      icon-color="#722ed1"
      :items="agentSidebarItems"
      :active-key="selectedAgentId ?? undefined"
      :loading="agentsLoading"
      :searchable="true"
      search-placeholder="搜索智能体..."
      :show-description="true"
      :createable="false"
      @change="handleAgentChange"
    />

    <!-- 中间：会话列表 -->
    <ConversationList
      v-if="selectedAgentId"
      :conversations="conversations"
      :loading="conversationsLoading"
      :active-id="selectedConversationId"
      @select="handleConversationSelect"
      @delete="handleConversationDelete"
      @rename="handleConversationRename"
      @create="handleCreateConversation"
    />

    <!-- 右侧：对话区域 -->
    <main class="chat-main">
      <!-- 顶部操作栏 -->
      <div v-if="selectedAgent" class="chat-header">
        <div class="header-info">
          <span class="agent-name">{{ selectedAgent.name }}</span>
          <span v-if="selectedConversation" class="conversation-title">
            / {{ selectedConversation.title }}
          </span>
        </div>
        <Button
          v-if="selectedConversationId"
          type="primary"
          size="small"
          @click="handleCreateConversation"
        >
          <PlusOutlined /> 新对话
        </Button>
      </div>

      <!-- 聊天内容 -->
      <div class="chat-content">
        <!-- 欢迎页：只在完全没有任何内容时显示 -->
        <AgentWelcome
          v-if="messages.length === 0 && !isStreaming && !selectedConversationId"
          :agent="selectedAgent"
          @send="handleWelcomeSend"
        />

        <!-- 消息列表：有消息、或正在流式输出、或已选中会话时显示 -->
        <ChatMessages
          v-else-if="messages.length > 0 || isStreaming || selectedConversationId"
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
        v-model:deep-thinking="enableDeepThinking"
        v-model:web-search="enableWebSearch"
        :loading="isStreaming"
        :show-model-select="false"
        @submit="handleSend"
        @cancel="handleStop"
      />
    </main>
  </div>
</template>

<style lang="less" scoped>
.agent-workbench {
  display: flex;
  height: calc(100vh - 140px);
  margin: 10px;
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

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid #f0f0f0;

  .header-info {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .agent-name {
    font-size: 16px;
    font-weight: 600;
    color: #333;
  }

  .conversation-title {
    font-size: 14px;
    color: #666;
  }
}

.chat-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;

  // 防止内容切换时抖动
  > * {
    flex: 1;
    min-height: 0;
  }
}
</style>
