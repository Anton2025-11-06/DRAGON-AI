<script lang="ts" setup>
/**
 * SOP 验证台：左侧流程概览（节点列表：名称/执行者/检查者/驳回次数）+ 右侧对话。
 *
 * 对话与深度探索「一模一样」：复用 /api/harness/chat 建轮 + EventSource 订阅 +
 * interrupt 停止，只多带 orchMode=sop + orchConfigId，worker 据此跑 SOPEngine
 * （逐节点顺序执行，所有节点共用一份 state）。配置改动走编辑抽屉，这里只呈现 + 验证。
 */
import { reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';

const props = defineProps<{ open: boolean; sopId?: null | number }>();
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>();

interface ChatMsg {
  role: 'assistant' | 'user';
  content: string;
  thinking?: string;
  tools: string[];
  streaming?: boolean;
}

interface StepLine {
  subject: string;
  executor: string;
  verifier: string;
  maxAttempts: number;
}

const info = reactive<{ name: string; purpose: string; steps: StepLine[] }>({
  name: '',
  purpose: '',
  steps: [],
});
const sessionId = ref<number | null>(null);
// SOP 无单一模型：发消息仍需 modelId（worker 流水线按各节点执行者模型跑，此值仅占位）
const fallbackModelId = ref<number | null>(null);
const messages = ref<ChatMsg[]>([]);
const input = ref('');
const running = ref(false);
let es: null | EventSource = null;
let currentTurnId = '';

async function loadDetail() {
  if (!props.sopId) return;
  const d: any = await api.GetDetail(props.sopId);
  const cfg = d.config || {};
  Object.assign(info, {
    name: d.name || '',
    purpose: d.purpose || '',
    steps: (cfg.steps || []).map((s: any) => ({
      subject: s.subject || '—',
      executor: (s.executor || {}).name || '—',
      verifier: s.verifier ? (s.verifier || {}).name || '—' : '',
      maxAttempts: s.max_attempts ?? 3,
    })),
  });
  const models: any = await api.ListModels();
  fallbackModelId.value =
    cfg.steps?.[0]?.executor?.mounts?.modelId ?? models.items?.[0]?.id ?? null;
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
  const s: any = await api.CreateSession(`验证-${info.name}`);
  sessionId.value = s.id;
  return s.id;
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
  if (!props.sopId || running.value) return;
  if (!fallbackModelId.value) {
    message.warning('未取到可用模型，无法发起验证');
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
      modelId: fallbackModelId.value,
      orchConfigId: props.sopId,
      orchMode: 'sop',
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
    title="SOP 验证台"
    wrap-class-name="orch-validate-modal"
    @update:open="close"
  >
    <div class="validate-body">
      <div class="config-pane">
        <a-descriptions :column="1" size="small" title="SOP 配置">
          <a-descriptions-item label="名称">{{ info.name }}</a-descriptions-item>
          <a-descriptions-item label="用途">{{ info.purpose || '—' }}</a-descriptions-item>
        </a-descriptions>
        <div class="field-label">流程节点（{{ info.steps.length }}）</div>
        <div v-for="(s, i) in info.steps" :key="i" class="step-line">
          <div class="step-name">{{ i + 1 }}. {{ s.subject }}</div>
          <div class="step-meta">执行者：{{ s.executor }}｜检查者：{{ s.verifier || '—' }}</div>
          <div class="step-meta">最大驳回：{{ s.maxAttempts }}</div>
        </div>
      </div>

      <div class="chat-pane">
        <div class="chat-list">
          <div v-for="(m, i) in messages" :key="i" :class="['bubble', m.role]">
            <div v-if="m.thinking" class="thinking">{{ m.thinking }}</div>
            <div v-if="m.tools.length" class="tools">
              <div v-for="(t, ti) in m.tools" :key="ti" class="tool-line">🔧 {{ t }}</div>
            </div>
            <div class="content">{{ m.content }}<span v-if="m.streaming">▋</span></div>
          </div>
          <div v-if="!messages.length" class="empty">
            发一条任务，流程按节点顺序执行，逐节点由执行者产出、检查者判定
          </div>
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
  padding-right: 8px;
  overflow: auto;
  border-right: 1px solid #f0f0f0;

  .field-label {
    margin: 12px 0 4px;
    font-size: 12px;
    color: #8c8c8c;
  }

  .step-line {
    padding: 6px 0;
    border-bottom: 1px dashed #f0f0f0;

    .step-name {
      font-weight: 500;
    }

    .step-meta {
      font-size: 12px;
      color: #8c8c8c;
    }
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
