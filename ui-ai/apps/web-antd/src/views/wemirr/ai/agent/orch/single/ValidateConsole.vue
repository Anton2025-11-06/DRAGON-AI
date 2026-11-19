<script lang="ts" setup>
/**
 * 单智能体验证台：左侧配置只读镜像（可改名称/用途/系统提示词并保存）+ 右侧对话。
 *
 * 对话与深度探索「一模一样」：复用 /api/harness/chat 建轮 + EventSource 订阅事件流
 * + interrupt 停止，只是多带 orchMode=single + orchConfigId，worker 用这份配置跑单 Agent。
 * 「边改边验」：左侧改了系统提示词点保存（走 UpdateObj），下一轮对话即用新配置
 * （worker 每轮按 config_id 现读 OrchAgent，不缓存），无需重开。
 */
import { reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';

const props = defineProps<{ agentId?: null | number; open: boolean }>();
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>();

interface ChatMsg {
  role: 'assistant' | 'user';
  content: string;
  thinking?: string;
  tools: string[];
  streaming?: boolean;
}

const detail = reactive({ name: '', purpose: '', systemPrompt: '' });
const saving = ref(false);
const sessionId = ref<number | null>(null);
const modelId = ref<number | null>(null);
const messages = ref<ChatMsg[]>([]);
const input = ref('');
const running = ref(false);
let es: null | EventSource = null;
let currentTurnId = '';

async function loadDetail() {
  if (!props.agentId) return;
  const d: any = await api.GetDetail(props.agentId);
  Object.assign(detail, {
    name: d.name || '',
    purpose: d.purpose || '',
    systemPrompt: d.systemPrompt || '',
  });
  modelId.value = d.mounts?.modelId ?? null;
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      closeStream();
      return;
    }
    messages.value = [];
    sessionId.value = null;
    await loadDetail();
  },
);

async function ensureSession() {
  if (sessionId.value) return sessionId.value;
  const s: any = await api.CreateSession(`验证-${detail.name}`);
  sessionId.value = s.id;
  return s.id;
}

async function handleSave() {
  if (!props.agentId) return;
  saving.value = true;
  try {
    const d: any = await api.GetDetail(props.agentId);
    await api.UpdateObj(props.agentId, {
      name: detail.name,
      purpose: detail.purpose || undefined,
      systemPrompt: detail.systemPrompt || undefined,
      mounts: d.mounts,
    });
    message.success('配置已保存，下一轮对话即生效');
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  } finally {
    saving.value = false;
  }
}

function closeStream() {
  if (es) {
    es.close();
    es = null;
  }
}

function subscribe(turnId: string) {
  currentTurnId = turnId;
  const assistant: ChatMsg = { content: '', role: 'assistant', streaming: true, tools: [] };
  messages.value.push(assistant);
  es = new EventSource(api.SubscribeUrl(turnId));
  es.onmessage = (ev) => {
    let frame: any;
    try {
      frame = JSON.parse(ev.data);
    } catch {
      return;
    }
    switch (frame.type) {
      case 'text.delta': {
        assistant.content += frame.content || '';
        break;
      }
      case 'thinking.delta': {
        assistant.thinking = (assistant.thinking || '') + (frame.content || '');
        break;
      }
      case 'tool.completed': {
        if (frame.tool) assistant.tools.push(frame.purpose || frame.tool);
        break;
      }
      case 'turn.completed': {
        if (frame.content) assistant.content = frame.content;
        finishTurn();
        break;
      }
      case 'turn.interrupted': {
        assistant.content += `\n\n（已中断：${frame.question || frame.reason || ''}）`;
        finishTurn();
        break;
      }
      case 'turn.failed': {
        assistant.content += `\n\n（失败：${frame.error || ''}）`;
        finishTurn();
        break;
      }
    }
  };
  es.onerror = () => finishTurn();
}

function finishTurn() {
  const last = messages.value[messages.value.length - 1];
  if (last) last.streaming = false;
  running.value = false;
  closeStream();
}

async function handleSend() {
  if (!props.agentId || running.value) return;
  if (!modelId.value) {
    message.warning('该智能体未配置模型，请先在编辑里选择模型');
    return;
  }
  const text = input.value.trim();
  if (!text) return;
  try {
    const sid = await ensureSession();
    running.value = true;
    messages.value.push({ content: text, role: 'user', tools: [] });
    input.value = '';
    const res: any = await api.OrchChat({
      message: text,
      modelId: modelId.value,
      orchConfigId: props.agentId,
      orchMode: 'single',
      sessionId: sid,
    });
    subscribe(res.turnId);
  } catch (error: any) {
    running.value = false;
    message.error(error?.message || '发送失败');
  }
}

async function handleStop() {
  if (!currentTurnId) return;
  try {
    await api.OrchInterrupt(currentTurnId);
  } catch {
    // 停止是尽力而为，终态帧由服务端下发
  }
}

function close() {
  closeStream();
  emit('update:open', false);
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="1100"
    title="智能体验证台"
    wrap-class-name="orch-validate-modal"
    @update:open="close"
  >
    <div class="validate-body">
      <div class="config-pane">
        <a-descriptions :column="1" size="small" title="配置镜像">
          <a-descriptions-item label="名称">{{ detail.name }}</a-descriptions-item>
          <a-descriptions-item label="用途">{{ detail.purpose || '—' }}</a-descriptions-item>
        </a-descriptions>
        <div class="field-label">系统提示词（改后保存即生效）</div>
        <a-textarea
          v-model:value="detail.systemPrompt"
          :autosize="{ minRows: 8, maxRows: 16 }"
          placeholder="留空则沿用平台默认基础段"
        />
        <a-button :loading="saving" block class="save-btn" type="primary" @click="handleSave">
          保存配置
        </a-button>
      </div>

      <div class="chat-pane">
        <div class="chat-list">
          <div
            v-for="(m, i) in messages"
            :key="i"
            :class="['bubble', m.role]"
          >
            <div v-if="m.thinking" class="thinking">{{ m.thinking }}</div>
            <div v-if="m.tools.length" class="tools">
              <div v-for="(t, ti) in m.tools" :key="ti" class="tool-line">🔧 {{ t }}</div>
            </div>
            <div class="content">{{ m.content }}<span v-if="m.streaming">▋</span></div>
          </div>
          <div v-if="!messages.length" class="empty">发一条消息，用当前配置验证效果</div>
        </div>
        <div class="chat-input">
          <a-textarea
            v-model:value="input"
            :auto-size="{ minRows: 2, maxRows: 4 }"
            placeholder="输入验证指令，Enter 发送"
            @press-enter.prevent="handleSend"
          />
          <div class="input-actions">
            <a-button v-if="running" danger @click="handleStop">停止</a-button>
            <a-button :loading="running" type="primary" @click="handleSend">发送</a-button>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
.validate-body {
  display: flex;
  gap: 16px;
  height: 60vh;
}

.config-pane {
  flex: 0 0 360px;
  overflow: auto;
  padding-right: 8px;
  border-right: 1px solid #f0f0f0;

  .field-label {
    margin: 12px 0 4px;
    font-size: 12px;
    color: #8c8c8c;
  }

  .save-btn {
    margin-top: 12px;
  }
}

.chat-pane {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;

  .chat-list {
    flex: 1;
    padding: 4px;
    overflow: auto;

    .empty {
      margin-top: 40%;
      color: #bfbfbf;
      text-align: center;
    }

    .bubble {
      max-width: 90%;
      padding: 8px 12px;
      margin-bottom: 12px;
      border-radius: 8px;

      &.user {
        background: #e6f4ff;
      }

      &.assistant {
        background: #f6f6f6;
      }

      .thinking {
        margin-bottom: 6px;
        font-size: 12px;
        color: #999;
        white-space: pre-wrap;
      }

      .tools .tool-line {
        font-size: 12px;
        color: #7c5cff;
      }

      .content {
        white-space: pre-wrap;
        word-break: break-word;
      }
    }
  }

  .chat-input {
    padding-top: 8px;
    border-top: 1px solid #f0f0f0;

    .input-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      margin-top: 8px;
    }
  }
}
</style>
