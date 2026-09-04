<script setup lang="ts" name="ModelPlazaChatPage">
import type { ChatMessage } from '../../chat/components';
import type { MyKeyRep } from '../api';
import type { ChatMessageSaveItem, ChatSessionRep, ModelChatMsg } from './api';

import { computed, onMounted, ref, watch } from 'vue';

import { Alert, message } from 'ant-design-vue';

import {
  ChatInput,
  ChatMessages,
  ChatSidebar,
  ChatWelcome,
} from '../../chat/components';
import { GetMyKeys } from '../api';
import {
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  updateSession as apiUpdateSession,
  chatWithModel,
  getSessionMessages,
  getSessions,
  saveSessionMessages,
} from './api';

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

// 已授权模型（my-keys：apply_id 作选择器 value，api_key 作网关鉴权）
const myKeys = ref<MyKeyRep[]>([]);
// 会话列表（恢复偏好快照：模型/深度思考/流式）
const sessions = ref<ChatSessionRep[]>([]);
const activeSessionId = ref<null | number>(null);
const sessionsLoading = ref(false);
// 消息缓存：sessionId -> ChatMessage[]
const messagesMap = ref<Record<number, ChatMessage[]>>({});
const messagesLoading = ref(false);

// 输入相关
const inputValue = ref('');
const isStreaming = ref(false);

// 偏好（选中的模型/深度思考/联网搜索/流式输出）
const selectedApplyId = ref<number | undefined>(undefined);
const deepThinking = ref(false);
const webSearch = ref(false);
const streamSwitch = ref(true);

// 流式输出
const streamingContent = ref('');
const streamingThinking = ref('');
let abortCtrl: AbortController | null = null;

// ==================== 计算属性 ====================

/** 模型选择器选项（id 用 apply_id，名称带分类后缀便于区分） */
const modelOptions = computed(() =>
  myKeys.value.map((k) => ({
    id: k.apply_id,
    provider: k.provider,
    type: k.category,
    name: `${k.name}（${k.category_label}）`,
    baseUrl: k.base_url,
    is_direct: k.is_direct !== false,
    suffixes: k.suffixes || [],
  })),
);

/** 当前选中模型（含网关鉴权 api_key） */
const currentModel = computed<MyKeyRep | undefined>(() =>
  myKeys.value.find((k) => k.apply_id === selectedApplyId.value),
);

// 非直连模型选中的接口后缀（默认第一个）；直连模型无后缀下拉
const selectedSuffix = ref('');
/** 当前模型支持的接口后缀选项（非直连时非空） */
const suffixOptions = computed<{ desc?: string; url: string }[]>(() => {
  const model = currentModel.value;
  if (!model || model.is_direct !== false) return [];
  return (model.suffixes || [])
    .map((s) => ({ url: s.url, desc: s.desc }))
    .filter((s) => s.url);
});

// 切换模型时重置接口后缀为第一个（新建会话/切换会话/默认选中都会触发）
watch(selectedApplyId, () => {
  selectedSuffix.value = suffixOptions.value[0]?.url || '';
});

/** 当前会话消息 */
const currentMessages = computed(
  () => messagesMap.value[activeSessionId.value ?? -1] || [],
);

/** 会话按创建时间分组（今天/昨天/近七天/更早） */
const groupedSessions = computed<ConversationGroup[]>(() => {
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

  sessions.value.forEach((s) => {
    const date = new Date(s.create_time);
    const entry = {
      item: { key: String(s.id), label: s.title || '新会话' },
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

  return Object.entries(groups)
    .filter(([, entries]) => entries && entries.length > 0)
    .map(([title, entries]) => ({
      title,
      items: entries!.toSorted((a, b) => b.time - a.time).map((e) => e.item),
    }));
});

// ==================== 生命周期 ====================

onMounted(async () => {
  await loadMyKeys();
  // 会话列表按登录用户隔离（auth 头自动携带 token），模型列表先加载供默认选中
  if (myKeys.value.length > 0) {
    await loadSessions();
  }
});

// 会话切换 - 消息未缓存时加载
watch(activeSessionId, async (sessionId) => {
  if (sessionId !== null && !messagesMap.value[sessionId]) {
    await loadMessages(sessionId);
  }
});

// ==================== 数据加载 ====================

async function loadMyKeys() {
  try {
    myKeys.value = await GetMyKeys();
    // 默认选中第一个已授权模型
    if (selectedApplyId.value === undefined && myKeys.value.length > 0) {
      selectedApplyId.value = myKeys.value[0]!.apply_id;
    }
  } catch {
    message.error('加载已授权模型失败');
  }
}

async function loadSessions() {
  try {
    sessionsLoading.value = true;
    sessions.value = await getSessions();
    // 只在没有选中会话时自动选中第一个
    if (activeSessionId.value === null && sessions.value.length > 0) {
      activeSessionId.value = sessions.value[0]!.id;
    }
  } catch {
    message.error('加载会话列表失败');
  } finally {
    sessionsLoading.value = false;
  }
}

async function loadMessages(sessionId: number) {
  try {
    messagesLoading.value = true;
    const items = await getSessionMessages(sessionId);
    messagesMap.value[sessionId] = items.map((m) => ({
      key: `msg-${m.id}`,
      role: m.role === 'USER' ? 'user' : 'ai',
      content: m.content || '',
      thinking: m.reasoning_content || '',
    }));
  } catch {
    messagesMap.value[sessionId] = [];
  } finally {
    messagesLoading.value = false;
  }
}

// ==================== 会话管理 ====================

/** 新建会话：立即创建（携带当前模型/偏好快照）并切换到新会话 */
async function handleCreateConversation() {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  const model = currentModel.value;
  if (!model) {
    message.warning('请先在下方选择已授权的模型');
    return;
  }
  try {
    const res = await apiCreateSession({
      model_apply_id: model.apply_id,
      model_name: model.model_name,
      reasoning: deepThinking.value,
      stream: streamSwitch.value,
    });
    messagesMap.value[res.id] = [];
    activeSessionId.value = res.id;
    await loadSessions();
  } catch {
    message.error('新建会话失败');
  }
}

/** 切换会话：恢复该会话保存的模型/深度思考/流式偏好 */
function handleSessionChange(key: string) {
  if (isStreaming.value) {
    message.warning('请等待当前对话完成');
    return;
  }
  const sessionId = Number(key);
  activeSessionId.value = sessionId;
  const s = sessions.value.find((x) => x.id === sessionId);
  if (s) {
    const stillValid = myKeys.value.some(
      (k) => k.apply_id === s.model_apply_id,
    );
    selectedApplyId.value = stillValid ? s.model_apply_id : undefined;
    deepThinking.value = s.reasoning;
    streamSwitch.value = s.stream;
  }
}

async function handleDeleteSession(key: string) {
  try {
    const sessionId = Number(key);
    await apiDeleteSession(sessionId);
    delete messagesMap.value[sessionId];
    sessions.value = sessions.value.filter((s) => s.id !== sessionId);
    if (activeSessionId.value === sessionId) {
      activeSessionId.value = sessions.value[0]?.id || null;
    }
  } catch {
    message.error('删除会话失败');
  }
}

function handleRenameSession(key: string, name: string) {
  const sessionId = Number(key);
  const s = sessions.value.find((x) => x.id === sessionId);
  if (!s) return;
  const oldTitle = s.title;
  s.title = name;
  apiUpdateSession(sessionId, { title: name }).catch(() => {
    s.title = oldTitle;
    message.error('重命名失败');
  });
}

// ==================== 对话请求 ====================

async function handleSend(value: string) {
  const content = value.trim();
  if (!content || isStreaming.value) return;
  const model = currentModel.value;
  if (!model) {
    message.warning('请先在下方选择已授权的模型');
    return;
  }
  // 非直连模型必须选接口后缀（未配置则后端无法转发，直接提示）
  if (model.is_direct === false && suffixOptions.value.length === 0) {
    message.warning('该模型未配置接口后缀，请联系管理员');
    return;
  }

  // 无活动会话时先创建（携带模型/偏好快照）
  let sessionId: null | number = activeSessionId.value;
  if (sessionId === null) {
    try {
      const res = await apiCreateSession({
        model_apply_id: model.apply_id,
        model_name: model.model_name,
        reasoning: deepThinking.value,
        stream: streamSwitch.value,
      });
      sessionId = res.id;
      activeSessionId.value = sessionId;
      messagesMap.value[sessionId] = [];
    } catch {
      message.error('创建会话失败，请重试');
      return;
    }
  }
  if (sessionId === null) return;
  const sid = sessionId;

  // 同步会话偏好（保持快照与当前选择一致）
  const session = sessions.value.find((s) => s.id === sid);
  if (session) {
    session.model_apply_id = model.apply_id;
    session.model_name = model.model_name;
    session.reasoning = deepThinking.value;
    session.stream = streamSwitch.value;
  }

  // 组装上下文（历史 + 新问题）后发给网关
  const history = messagesMap.value[sid] || [];
  const contextMsgs: ModelChatMsg[] = history.map((m) => ({
    role: m.role === 'user' ? 'user' : 'assistant',
    content: m.content,
  }));
  contextMsgs.push({ role: 'user', content });

  // 视图：用户消息 + AI 占位
  if (!messagesMap.value[sid]) {
    messagesMap.value[sid] = [];
  }
  const list = messagesMap.value[sid]!;
  list.push({ key: `user-${Date.now()}`, role: 'user', content });
  const aiKey = `ai-${Date.now()}`;
  list.push({ key: aiKey, role: 'ai', content: '', thinking: '' });

  inputValue.value = '';
  streamingContent.value = '';
  streamingThinking.value = '';
  let accContent = '';
  let accThinking = '';
  let errorMsg = '';
  // 调用失败时上游返回的完整接口数据（code/message/data），在 AI 气泡下方展示
  let errorData: unknown;
  isStreaming.value = true;

  try {
    abortCtrl = new AbortController();
    await chatWithModel(
      {
        model: model.model_name,
        apiKey: model.api_key,
        messages: contextMsgs,
        // 直连调 /api/model；非直连调 /api/model + 选中后缀
        isDirect: model.is_direct !== false,
        suffix:
          model.is_direct === false
            ? selectedSuffix.value || undefined
            : undefined,
        stream: streamSwitch.value,
        reasoning: deepThinking.value,
        search: webSearch.value,
      },
      (chunk) => {
        if (chunk.done) {
          // 非流式：一次性拿到全文；流式：结束标记无需追加
          if (!streamSwitch.value) {
            accContent = chunk.content;
            accThinking = chunk.reasoningContent;
            streamingContent.value = accContent;
            streamingThinking.value = accThinking;
          }
          return;
        }
        accContent += chunk.content;
        accThinking += chunk.reasoningContent;
        streamingContent.value = accContent;
        streamingThinking.value = accThinking;
      },
      abortCtrl.signal,
    );
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      message.info('已停止生成');
    } else {
      errorMsg = error?.message || '请求失败，请重试';
      // 接口返回过响应体（ModelChatError）时保留，供消息下方展示完整接口数据
      errorData = error?.responseData;
    }
  } finally {
    abortCtrl = null;
  }

  // 固化 AI 消息（错误时展示错误文案）
  const finalContent = errorMsg ? `❌ ${errorMsg}` : accContent;
  const aiMsg = messagesMap.value[sid]?.find((m) => m.key === aiKey);
  if (aiMsg) {
    aiMsg.content = finalContent;
    aiMsg.thinking = accThinking;
    if (errorData !== undefined) {
      aiMsg.errorData = errorData;
    }
  }
  isStreaming.value = false;

  // 持久化本轮消息（首条用户消息会自动生成会话标题）
  try {
    const saveItems: ChatMessageSaveItem[] = [
      { role: 'USER', content },
      {
        role: 'ASSISTANT',
        content: finalContent,
        reasoning_content: accThinking,
        model_name: model.model_name,
      },
    ];
    await saveSessionMessages(sid, saveItems);
    // await loadSessions();
  } catch {
    message.warning('消息保存失败');
  }
}

function handleStop() {
  abortCtrl?.abort();
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
  <div class="model-chat-wrapper">
    <div class="model-chat-page">
      <!-- 左侧会话列表 -->
      <ChatSidebar
        class="model-chat-sidebar"
        :grouped-conversations="groupedSessions"
        :active-key="activeSessionId != null ? String(activeSessionId) : ''"
        :loading="sessionsLoading"
        :is-empty="sessions.length === 0"
        :total="sessions.length"
        :has-more="false"
        @create="handleCreateConversation"
        @change="handleSessionChange"
        @delete="handleDeleteSession"
        @rename="handleRenameSession"
      />

      <!-- 右侧聊天区域 -->
      <main class="model-chat-main">
        <div class="model-chat-content">
          <!-- 无已授权模型提示 -->
          <Alert
            v-if="myKeys.length === 0"
            type="warning"
            show-icon
            message="暂无已授权模型"
            description="请先到「模型列表」申请模型并审批通过后，再回来发起对话。"
            class="model-chat-alert"
          />

          <!-- 欢迎页 -->
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
            :is-streaming="isStreaming"
            @retry="handleRetry"
          />
        </div>

        <!-- 输入区域（模型选择/深度思考/流式输出） -->
        <ChatInput
          v-model:value="inputValue"
          v-model:model-id="selectedApplyId"
          v-model:deep-thinking="deepThinking"
          v-model:web-search="webSearch"
          v-model:stream="streamSwitch"
          v-model:suffix-id="selectedSuffix"
          :models="modelOptions"
          :suffix-options="suffixOptions"
          :loading="isStreaming"
          :show-stream-switch="true"
          @submit="handleSend"
          @cancel="handleStop"
        />
      </main>
    </div>
  </div>
</template>

<style lang="less" scoped>
.model-chat-wrapper {
  height: calc(100vh - 140px);
  padding: 10px;
  background: var(--background-color, #f5f7f9);
}

.model-chat-page {
  display: flex;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
  border-radius: 8px;
  box-shadow: 0 1px 2px rgb(0 0 0 / 3%);
}

.model-chat-sidebar {
  width: 280px;
  min-width: 280px;
  border-radius: 8px 0 0 8px;
}

.model-chat-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
}

.model-chat-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.model-chat-alert {
  margin: 16px 24px 0;
}
</style>
