<script setup lang="ts">
/**
 * 工作流对话窗口（列表页「去对话」）。
 *
 * 只做三件事：
 * 1. 按 START 节点配置渲染全部入参入口（含文件上传，走 workflow 模块上传接口）；
 * 2. 发送时调 execute-async，并订阅 subscribe 事件流，边收边渲染；
 * 3. 把每一步节点输出与最终 outputs 交给 OutputValue 按值形态渲染，
 *    因此新增节点类型 / 模型能力类型不需要改这个组件。
 */
import type { ChatRun } from './useChatExecution';

import type { ApiKeyListResp } from '#/api/ai-workflow';
import type { InputField, WorkflowPageResp } from '#/api/ai-workflow/types';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';

import {
  DeleteOutlined,
  KeyOutlined,
  LoadingOutlined,
  PartitionOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { cancelExecution, getWorkflowDetail } from '#/api/ai-workflow';

import DynamicInputForm from '../debug/DynamicInputForm.vue';
import MarkdownRenderer from '../debug/MarkdownRenderer.vue';
import { formatDuration } from './chat-output';
import ExecutionStepsModal from './ExecutionStepsModal.vue';
import OutputValue from './OutputValue.vue';
import { useChatExecution } from './useChatExecution';

interface ChatMessage {
  id: string;
  /** 用户消息：本次提交的入参 */
  inputs?: Record<string, any>;
  role: 'assistant' | 'user';
  /** 助手消息：对应一次执行 */
  run?: ChatRun;
  time: string;
}

const props = defineProps<{
  apiKey?: ApiKeyListResp | null;
  open: boolean;
  workflow?: null | WorkflowPageResp;
}>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

const formRef = ref<InstanceType<typeof DynamicInputForm>>();
const bodyRef = ref<HTMLElement>();
const fields = ref<InputField[]>([]);
const loading = ref(false);
const messages = ref<ChatMessage[]>([]);
const formValues = ref<Record<string, any>>({});
/** 执行过程明细弹窗 */
const stepsOpen = ref(false);
const stepsRun = ref<ChatRun | null>(null);

/** 节点ID → 画布节点名（异步事件只带 nodeId） */
const nodeLabels = ref<Record<string, string>>({});

/** 作为正文优先展示的输出口（END 回答模板 / REPLY / 单一文本输出常见键名） */
const ANSWER_KEYS = ['answer', 'text', 'result'] as const;

const chatExecution = useChatExecution({
  resolveLabel: (nodeId, nodeType) =>
    nodeLabels.value[nodeId] || nodeType || nodeId,
});

const running = computed(() => chatExecution.running.value);

function nowTime(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function close(open: boolean) {
  emit('update:open', open);
}

// ==================== 工作流元数据加载 ====================

/**
 * 取 START 节点入参定义 + 节点名映射。
 * 字段可能在 data.config.fields 或 data.fields，两种历史写法都兼容（与编辑页一致）。
 */
function applyGraph(graph: any) {
  const nodes: any[] = graph?.nodes || [];
  const labelMap: Record<string, string> = {};
  nodes.forEach((node) => {
    if (!node?.id) return;
    labelMap[node.id] = node.data?.label || node.label || node.data?.name || '';
  });
  nodeLabels.value = labelMap;

  const startNode = nodes.find(
    (node) => node?.type === 'START' || node?.data?.nodeType === 'START',
  );
  const rawFields =
    startNode?.data?.config?.fields || startNode?.data?.fields || [];
  fields.value = (rawFields as any[]).map((field) => ({
    allowedFileTypes: field.allowedFileTypes,
    defaultValue: field.defaultValue,
    description: field.description || field.label,
    label: field.label || field.name,
    maxFileCount: field.maxFileCount,
    maxFileSize: field.maxFileSize,
    maxLength: field.maxLength,
    maxValue: field.maxValue,
    minValue: field.minValue,
    name: field.name,
    options: field.options,
    pattern: field.pattern,
    patternMessage: field.patternMessage,
    required: field.required || false,
    type: field.type || 'TEXT',
  }));
}

async function loadWorkflow() {
  const workflowId = props.workflow?.id;
  if (!workflowId) return;
  loading.value = true;
  try {
    const detail = await getWorkflowDetail(workflowId);
    applyGraph(detail?.graph);
  } catch {
    message.error('加载工作流入参失败');
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      chatExecution.disconnect();
      stepsOpen.value = false;
      stepsRun.value = null;
      return;
    }
    messages.value = [];
    formValues.value = {};
    await loadWorkflow();
  },
);

// ==================== 展示辅助 ====================

function scrollToBottom() {
  nextTick(() => {
    const el = bodyRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

/** 入参里的文件值（上传产物是对象/对象数组） */
function isFileValue(value: any): boolean {
  if (Array.isArray(value)) return value.every((item) => isFileValue(item));
  return !!value && typeof value === 'object' && typeof value.url === 'string';
}

function fileText(value: any): string {
  const files: any[] = Array.isArray(value) ? value : [value];
  return files.map((item) => item?.name || '文件').join('、');
}

/** 用户气泡里的入参行：跳过空值，文件值折叠成文件名 */
function inputEntries(
  inputs: Record<string, any>,
): { label: string; value: any }[] {
  const labelOf = new Map(fields.value.map((item) => [item.name, item.label]));
  return Object.entries(inputs || {})
    .filter(([, value]) => {
      if (value === undefined || value === null || value === '') return false;
      return Array.isArray(value) ? value.length > 0 : true;
    })
    .map(([key, value]) => ({
      label: labelOf.get(key) || key,
      value: isFileValue(value) ? fileText(value) : value,
    }));
}

function pickAnswer(run: ChatRun): { key?: string; text: string } {
  const outputs = run.outputs || {};
  for (const key of ANSWER_KEYS) {
    const value = outputs[key];
    if (typeof value === 'string' && value.trim()) return { key, text: value };
  }
  // 没有终态正文时退回流式增量：END 直接引用节点输出的工作流也只有 token 流
  const streamed = Object.values(run.streams || {}).join('');
  return streamed.trim() ? { text: streamed } : { text: '' };
}

/** 正文之外的其余输出（向量、图片直链、结构化 JSON 等） */
function restOutputs(run: ChatRun): [string, any][] {
  const answer = pickAnswer(run);
  return Object.entries(run.outputs || {}).filter(
    ([key]) => key !== answer.key,
  );
}

function reasoningText(run: ChatRun): string {
  return Object.values(run.reasoning || {}).join('');
}

/** 打开执行过程明细（节点输出统一在弹窗里一块画布展开） */
function openSteps(run?: ChatRun | null) {
  stepsRun.value = run ?? null;
  stepsOpen.value = true;
}

// ==================== 发送 ====================

async function handleSend() {
  if (!props.workflow?.id) return;
  if (running.value) return;

  if (fields.value.length > 0) {
    const passed = await formRef.value?.validate();
    if (!passed) {
      message.warning('请完善入参');
      return;
    }
  }
  const inputs: Record<string, any> = { ...formValues.value };
  // 上传中不发送：该字段的文件值还没落地，发出去就是一个空参
  if (formRef.value?.hasUploading?.()) {
    message.warning('文件上传中，请稍候');
    return;
  }

  messages.value.push({
    id: `u_${Date.now()}`,
    inputs: structuredClone(inputs),
    role: 'user',
    time: nowTime(),
  });
  scrollToBottom();

  try {
    const run = await chatExecution.send(props.workflow.id, inputs);
    messages.value.push({
      id: `a_${Date.now()}`,
      role: 'assistant',
      run,
      time: nowTime(),
    });
    scrollToBottom();
    watchRun(run);
  } catch (error: any) {
    message.error(error?.message || '发起执行失败');
  }
}

/** 执行到终态后再滚一次，保证展开的步骤不遮挡输入框 */
function watchRun(run: ChatRun) {
  const stop = watch(
    () => run.status,
    (status) => {
      if (status !== 'running') {
        scrollToBottom();
        stop();
      }
    },
    { deep: false },
  );
}

async function handleStop() {
  const last = messages.value.toReversed().find((item) => item.run);
  const run = last?.run;
  if (!run) return;
  try {
    // 前端断开不等于后台停下，跳过这一步会让工作流继续跑完
    await cancelExecution(run.executionId);
  } catch {
    // 已经结束的执行，取消会报错，不影响本地收流
  }
  chatExecution.abort(run);
}

function handleClear() {
  if (running.value) {
    message.warning('执行中，请先停止');
    return;
  }
  messages.value = [];
  stepsRun.value = null;
}

/** DynamicInputForm 内部维护真实值，外层只接它的同步回调 */
function onValuesChange(values: Record<string, any>) {
  formValues.value = values;
}

/** 一次执行的终态文案 */
function runStateText(run: ChatRun): string {
  const map: Record<string, string> = {
    cancelled: '已取消',
    error: '执行失败',
    running: '执行中',
    success: '已完成',
  };
  return map[run.status] || run.status;
}

// ==================== 快捷键 ====================

/**
 * Ctrl / ⌘ + Enter 发送。
 * 监听挂在 window 上而不是某个输入框上：入参可能是文本、下拉、数字框甚至文件按钮，
 * 逐个绑定会漏掉焦点在消息区时的场景。
 */
function handleShortcut(event: KeyboardEvent) {
  if (!props.open || running.value) return;
  if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
  // 中文输入法确认候选词时同样会按下 Enter，别把选词当成发送
  if (event.isComposing) return;
  event.preventDefault();
  handleSend();
}

onMounted(() => window.addEventListener('keydown', handleShortcut));
onBeforeUnmount(() => window.removeEventListener('keydown', handleShortcut));
</script>

<template>
  <a-modal :footer="null" :open="props.open" :width="820" @update:open="close">
    <template #title>
      <div class="chat-title">
        <span class="title-text">{{
          props.workflow?.name || '工作流对话'
        }}</span>
        <a-tag v-if="props.apiKey" color="blue">
          <KeyOutlined /> {{ props.apiKey.name }}
        </a-tag>
        <a-button
          class="clear-btn"
          size="small"
          type="text"
          @click="handleClear"
        >
          <template #icon><DeleteOutlined /></template>
          清空
        </a-button>
      </div>
    </template>

    <a-spin :spinning="loading">
      <div ref="bodyRef" class="chat-body">
        <!-- 空状态：说明对话口径 -->
        <div v-if="messages.length === 0" class="chat-welcome">
          <div class="welcome-icon"><RobotOutlined /></div>
          <div class="welcome-title">
            {{ props.workflow?.name || '工作流' }}
          </div>
          <div class="welcome-desc">
            {{
              fields.length > 0
                ? '填写下方入参后发送，执行过程与结果会在这里实时展示'
                : '该工作流无需入参，直接发送即可开始执行'
            }}
          </div>
        </div>

        <div
          v-for="msg in messages"
          :key="msg.id"
          class="chat-row"
          :class="[msg.role]"
        >
          <div class="avatar">
            <UserOutlined v-if="msg.role === 'user'" />
            <RobotOutlined v-else />
          </div>

          <div class="bubble">
            <!-- 用户：本次入参 -->
            <template v-if="msg.role === 'user'">
              <div
                v-if="inputEntries(msg.inputs || {}).length === 0"
                class="bubble-line"
              >
                （无入参）
              </div>
              <OutputValue
                v-for="entry in inputEntries(msg.inputs || {})"
                :key="entry.label"
                :label="entry.label"
                :value="entry.value"
              />
            </template>

            <!-- 助手：执行过程 + 输出 -->
            <template v-else-if="msg.run">
              <div class="run-bar">
                <LoadingOutlined v-if="msg.run.status === 'running'" spin />
                <span class="run-state">{{ runStateText(msg.run) }}</span>
                <span class="run-cost">{{
                  formatDuration(msg.run.duration)
                }}</span>
                <span class="run-time">{{ msg.time }}</span>
              </div>

              <!-- 思维链 -->
              <div v-if="reasoningText(msg.run)" class="reasoning-box">
                <div class="reasoning-title">思考过程</div>
                <MarkdownRenderer :content="reasoningText(msg.run)" />
              </div>

              <!-- 正文 -->
              <div v-if="pickAnswer(msg.run).text" class="answer-box">
                <MarkdownRenderer :content="pickAnswer(msg.run).text" />
                <span v-if="msg.run.status === 'running'" class="typing"></span>
              </div>

              <!-- 其余输出：任何节点/模型类型都按值形态渲染 -->
              <div v-if="restOutputs(msg.run).length > 0" class="outputs-box">
                <OutputValue
                  v-for="[key, value] in restOutputs(msg.run)"
                  :key="key"
                  :label="key"
                  :value="value"
                />
              </div>

              <a-alert
                v-if="msg.run.error"
                :message="msg.run.error"
                class="run-error"
                type="error"
              />
              <div
                v-if="
                  msg.run.status === 'running' &&
                  !pickAnswer(msg.run).text &&
                  msg.run.steps.length === 0
                "
                class="bubble-line placeholder"
              >
                正在排队执行…
              </div>

              <!-- 执行过程入口：右下角蓝色高亮，点开弹窗看各节点输出明细 -->
              <div v-if="msg.run.steps.length > 0" class="bubble-foot">
                <a class="steps-link" @click="openSteps(msg.run)">
                  <PartitionOutlined />
                  执行过程 · {{ msg.run.steps.length }} 个节点
                </a>
              </div>
            </template>
          </div>
        </div>
      </div>
    </a-spin>

    <!-- 输入区：START 节点全部入参 + 文件上传 -->
    <div class="chat-composer">
      <div v-if="fields.length > 0" class="composer-form">
        <DynamicInputForm
          ref="formRef"
          :fields="fields"
          :values="formValues"
          @update:values="onValuesChange"
        />
      </div>
      <div class="composer-actions">
        <span class="composer-tip">
          以 API Key「{{ props.apiKey?.name || '—' }}」调用已发布版本
        </span>
        <span class="composer-tip">Ctrl / ⌘ + Enter 发送</span>
        <a-button v-if="running" danger size="small" @click="handleStop">
          停止
        </a-button>
        <a-tooltip title="Ctrl / ⌘ + Enter 发送">
          <a-button
            :loading="running"
            size="small"
            type="primary"
            @click="handleSend"
          >
            <template #icon><SendOutlined /></template>
            发送
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <!-- 执行过程明细：所有节点输出同一块画布，节点之间虚线分隔 -->
    <ExecutionStepsModal v-model:open="stepsOpen" :run="stepsRun" />
  </a-modal>
</template>

<style lang="less" scoped>
.chat-title {
  display: flex;
  gap: 10px;
  align-items: center;

  // 标题区右侧让出关闭按钮的位置，否则「清空」会和右上角 X 重叠
  padding-right: 36px;

  .title-text {
    font-size: 15px;
    font-weight: 600;
  }

  .clear-btn {
    margin-left: auto;
    font-size: 12px;
    color: #8c8c8c;
  }
}

.chat-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 420px;
  padding: 16px 4px 8px;
  overflow-y: auto;
}

.chat-welcome {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  text-align: center;

  .welcome-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    margin-bottom: 6px;
    font-size: 22px;
    color: #1890ff;
    background: rgb(24 144 255 / 10%);
    border-radius: 50%;
  }

  .welcome-title {
    font-size: 15px;
    font-weight: 600;
    color: #262626;
  }

  .welcome-desc {
    max-width: 380px;
    font-size: 12px;
    color: #8c8c8c;
  }
}

.chat-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;

  &.user {
    flex-direction: row-reverse;

    .avatar {
      color: #fff;
      background: #1890ff;
    }

    .bubble {
      background: #e6f4ff;
      border-color: #bae0ff;
    }
  }

  .avatar {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    font-size: 14px;
    color: #722ed1;
    background: rgb(114 46 209 / 10%);
    border-radius: 8px;
  }

  .bubble {
    flex: 1;
    min-width: 0;
    padding: 10px 12px;
    overflow: hidden;
    overflow-wrap: anywhere;
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 10px;

    // 长地址、代码块、媒体一律收在气泡内，不越过两侧头像的内边界
    :deep(pre) {
      max-width: 100%;
      overflow-x: auto;
    }

    :deep(img),
    :deep(video) {
      max-width: 100%;
    }
  }
}

.bubble-line {
  font-size: 13px;
  color: #595959;

  &.placeholder {
    color: #bfbfbf;
  }
}

.run-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
  color: #8c8c8c;

  .run-state {
    font-weight: 500;
    color: #595959;
  }
}

.bubble-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;

  .steps-link {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    font-size: 12px;
    color: #1890ff;
    cursor: pointer;

    &:hover {
      color: #40a9ff;
    }
  }
}

.reasoning-box {
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #8c8c8c;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 8px;

  .reasoning-title {
    margin-bottom: 4px;
    font-weight: 500;
    color: #ad8b00;
  }
}

.answer-box {
  font-size: 13px;
  color: #262626;
  word-break: break-word;

  .typing {
    display: inline-block;
    width: 6px;
    height: 14px;
    margin-left: 2px;
    vertical-align: middle;
    background: #1890ff;
    animation: blink 1s steps(2) infinite;
  }
}

.outputs-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px dashed #f0f0f0;
}

.run-error {
  margin-top: 8px;
}

.chat-composer {
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;

  .composer-form {
    max-height: 240px;
    padding-right: 4px;
    overflow-y: auto;

    :deep(.ant-form-item) {
      margin-bottom: 12px;
    }

    :deep(.ant-form-item-label) {
      padding-bottom: 2px;
    }
  }

  .composer-actions {
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: flex-end;
    padding-top: 4px;
  }

  .composer-tip {
    font-size: 12px;
    color: #bfbfbf;

    // 只有左侧的调用口径说明顶开剩余空间，快捷键提示跟着按钮这组走
    &:first-child {
      margin-right: auto;
    }
  }
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}
</style>
