<script lang="ts" setup>
/**
 * 目标验证台：左侧目标配置只读概览（执行者 + 检查者）+ 右侧对话。
 *
 * 对话与深度探索「一模一样」：复用 /api/harness/chat 建轮 + EventSource 订阅 +
 * interrupt 停止，只多带 orchMode=goal + orchConfigId，worker 据此跑 GoalPipeline
 * （执行者产出、检查者判定，循环到通过 / 不可能）。配置改动走编辑抽屉，这里只呈现 + 验证。
 */
import { reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';

const props = defineProps<{ goalId?: null | number; open: boolean }>();
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>();

interface ChatMsg {
  role: 'assistant' | 'user';
  content: string;
  thinking?: string;
  tools: string[];
  streaming?: boolean;
}

const info = reactive<{
  executor: string;
  maxIters: number;
  name: string;
  purpose: string;
  verifier: string;
}>({
  name: '',
  purpose: '',
  executor: '',
  verifier: '',
  maxIters: 10,
});
const sessionId = ref<number | null>(null);
// 目标无单一模型：发消息仍需 modelId（worker 流水线按执行者/检查者各自模型跑，此值仅占位）
const fallbackModelId = ref<number | null>(null);
const messages = ref<ChatMsg[]>([]);
const input = ref('');
const running = ref(false);
let es: null | EventSource = null;
let currentTurnId = '';

async function loadDetail() {
  if (!props.goalId) return;
  const d: any = await api.GetDetail(props.goalId);
  const cfg = d.config || {};
  Object.assign(info, {
    name: d.name || '',
    purpose: d.purpose || '',
    executor: (cfg.executor || {}).name || '—',
    verifier: (cfg.verifier || {}).name || '—',
    maxIters: cfg.max_iters ?? 10,
  });
  const models: any = await api.ListModels();
  fallbackModelId.value =
    cfg.executor?.mounts?.modelId ?? models.items?.[0]?.id ?? null;
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
  if (!props.goalId || running.value) return;
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
      orchConfigId: props.goalId,
      orchMode: 'goal',
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
    title="目标验证台"
    wrap-class-name="orch-validate-modal"
    @update:open="close"
  >
    <div class="validate-body">
      <div class="config-pane">
        <a-descriptions :column="1" size="small" title="目标配置">
          <a-descriptions-item label="名称">{{ info.name }}</a-descriptions-item>
          <a-descriptions-item label="用途">{{ info.purpose || '—' }}</a-descriptions-item>
          <a-descriptions-item label="执行者">{{ info.executor }}</a-descriptions-item>
          <a-descriptions-item label="检查者">{{ info.verifier }}</a-descriptions-item>
          <a-descriptions-item label="最大迭代">{{ info.maxIters }}</a-descriptions-item>
        </a-descriptions>
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
            发一条目标，执行者产出、检查者判定，循环到通过
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
