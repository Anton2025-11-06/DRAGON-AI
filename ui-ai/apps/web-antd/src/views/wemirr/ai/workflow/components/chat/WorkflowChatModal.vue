<script setup lang="ts">
/**
 * 工作流对话窗口（列表页「去对话」）。
 *
 * 职责：
 * 1. 按 START 节点配置渲染全部入参入口（含文件上传，走 workflow 模块上传接口）；
 * 2. 发送时调 execute-async，并订阅 subscribe 事件流，边收边渲染；
 * 3. 把执行过程中每个节点的输出自动铺进气泡（一块一段、虚线分隔、各自可复制），
 *    完整输入 / 输出下钻放在右下角「执行过程」弹窗里。
 * 渲染统一走 OutputValue 按值形态适配，新增节点类型 / 模型能力类型不需要改这里。
 */
import type { ChatRun, ChatStep } from './useChatExecution';

import type { ApiKeyListResp } from '#/api/ai-workflow';
import type {
  ApprovalDecisionReq,
  InputField,
  WorkflowPageResp,
} from '#/api/ai-workflow/types';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';

import {
  CloseCircleFilled,
  CopyOutlined,
  DeleteOutlined,
  DownOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  KeyOutlined,
  LoadingOutlined,
  PartitionOutlined,
  RedoOutlined,
  RightOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons-vue';
import { message, Tooltip } from 'ant-design-vue';

import { getWorkflowDetail } from '#/api/ai-workflow';

import ApprovalPanel from '../debug/ApprovalPanel.vue';
import DynamicInputForm from '../debug/DynamicInputForm.vue';
import MarkdownRenderer from '../debug/MarkdownRenderer.vue';
import { copyToClipboard, formatDuration, toCopyText } from './chat-output';
import ExecutionStepsModal from './ExecutionStepsModal.vue';
import OutputValue from './OutputValue.vue';
import { useChatExecution } from './useChatExecution';

/** 气泡里一段节点数据的展示单元 */
interface RunDataBlock {
  /** 流式累积正文（Markdown 渲染） */
  answer?: string;
  copyText: string;
  duration?: number;
  error?: string;
  /** 并行分支里节点重名是常态，唯一 key 只能用 nodeId */
  key: string;
  /** 结构化输出 */
  output?: any;
  /** 思维链 */
  reasoning?: string;
  /** 折叠状态归属的 key（一轮执行里一个节点一份） */
  reasonKey?: string;
  running?: boolean;
  status?: string;
  title: string;
}

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
/** 全屏展示（清空按钮左边的入口，ESC 退出） */
const isFull = ref(false);
/** 思考过程的展开态，按「执行id:节点id」记，换一轮执行不串台 */
const openReasons = ref<Record<string, boolean>>({});

/** 节点ID → 画布节点名（异步事件只带 nodeId） */
const nodeLabels = ref<Record<string, string>>({});

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
    description: field.description,
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
      isFull.value = false;
      openReasons.value = {};
      return;
    }
    messages.value = [];
    formValues.value = {};
    // 重开窗不能沿用上一次的输入（已上传文件列表存在子组件里，只能让它自己清）
    formRef.value?.resetFields?.();
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

/** 用户气泡里的入参行：跳过空值，文件值折叠成文件名，复杂值折叠成 JSON 文本 */
function inputEntries(
  inputs: Record<string, any>,
): { label: string; value: string }[] {
  const labelOf = new Map(fields.value.map((item) => [item.name, item.label]));
  return Object.entries(inputs || {})
    .filter(([, value]) => {
      if (value === undefined || value === null || value === '') return false;
      return Array.isArray(value) ? value.length > 0 : true;
    })
    .map(([key, value]) => {
      let text: string;
      if (isFileValue(value)) text = fileText(value);
      else if (typeof value === 'object') text = JSON.stringify(value);
      else text = String(value);
      return { label: labelOf.get(key) || key, value: text };
    });
}

/** 有没有值得铺开的数据（空对象/空数组只占版面） */
function hasData(value: any): boolean {
  if (value === null || value === undefined || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function streamText(run: ChatRun, step: ChatStep): string {
  return run.streams?.[step.nodeId] || '';
}

function reasoningText(run: ChatRun, step: ChatStep): string {
  return run.reasoning?.[step.nodeId] || '';
}

function buildCopyText(answer?: string, output?: any): string {
  const parts: string[] = [];
  if (answer) parts.push(answer);
  if (hasData(output)) parts.push(toCopyText(output));
  return parts.join('\n\n');
}

/**
 * 气泡内要自动显示的数据块：执行过程里每一段有产出的节点各一块，按执行顺序排。
 * 最终 outputs 本质是 END/REPLY 节点输出的汇总，已由对应节点块覆盖时不再重复铺一份。
 */
function dataBlocks(run: ChatRun): RunDataBlock[] {
  const blocks: RunDataBlock[] = [];
  for (const step of run.steps) {
    const answer = streamText(run, step);
    const reasoning = reasoningText(run, step);
    const output = step.output;
    if (!answer && !reasoning && !hasData(output) && !step.error) continue;
    blocks.push({
      answer: answer || undefined,
      copyText: buildCopyText(answer, output),
      duration: step.duration,
      error: step.error,
      key: step.nodeId,
      output,
      reasonKey: `${run.executionId}:${step.nodeId}`,
      reasoning: reasoning || undefined,
      running: step.status === 'running',
      status: step.status,
      title: step.label,
    });
  }

  const outputs = run.outputs || {};
  const finalText = toCopyText(outputs);
  const covered = blocks.some(
    (block) => hasData(block.output) && toCopyText(block.output) === finalText,
  );
  if (Object.keys(outputs).length > 0 && !covered) {
    blocks.push({
      copyText: finalText,
      key: '__final__',
      output: outputs,
      title: '最终输出',
    });
  }
  return blocks;
}

async function copyBlock(block: RunDataBlock) {
  if (!block.copyText) {
    message.info('该段暂无可复制内容');
    return;
  }
  if (await copyToClipboard(block.copyText)) {
    message.success('已复制到剪贴板');
  } else {
    message.error('复制失败');
  }
}

/** 思考过程默认折叠，长思链不把气泡顶成一屏 */
function isReasonOpen(block: RunDataBlock): boolean {
  return !!openReasons.value[block.reasonKey || ''];
}

function toggleReason(block: RunDataBlock) {
  const key = block.reasonKey || '';
  openReasons.value = { ...openReasons.value, [key]: !openReasons.value[key] };
}

/** 思考过程的一句话概览（折叠时也要让人知道里面有多少东西） */
function reasonBrief(block: RunDataBlock): string {
  if (block.running) return '思考中…';
  return `${(block.reasoning || '').length} 字`;
}

/**
 * 用户气泡的入参快照。
 *
 * DynamicInputForm 往外同步值时是 `{ ...reactive }`，展开会经响应式 getter 取属性，
 * 文件字段这种嵌套值到外层已经是 Proxy（toRaw 只能拆最外层，拆不到已存进 target 的内层代理），
 * structuredClone 碰上 Proxy 直接抛 DataCloneError；JSON 序列化会透明解代理，
 * 而这份快照只用于展示，不需要保留引用类型，因此故意不用 structuredClone。
 */
function snapshotInputs(inputs: Record<string, any>): Record<string, any> {
  try {
    // eslint-disable-next-line unicorn/prefer-structured-clone -- 入参里有 Vue 代理对象，structuredClone 不可用
    return JSON.parse(JSON.stringify(inputs)) as Record<string, any>;
  } catch {
    return { ...inputs };
  }
}

/** 打开执行过程明细（输入 / 输出下钻在弹窗里逐层展开） */
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
    inputs: snapshotInputs(inputs),
    role: 'user',
    time: nowTime(),
  });
  scrollToBottom();

  try {
    // 带上选中的 Key：这次发起走网关的 API Key 模式（Key 有效性 + QPS 限流），
    // 和输入框下方「以 API Key「xx」调用已发布版本」的口径对上
    const run = await chatExecution.send(
      props.workflow.id,
      inputs,
      props.apiKey?.apiKey,
    );
    messages.value.push({
      id: `a_${Date.now()}`,
      role: 'assistant',
      run,
      time: nowTime(),
    });
    // 发起成功后清空入参区：本轮值已存进上面的用户气泡，输入区该为下一条让位
    formRef.value?.resetFields?.();
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
      // 暂停不是终态：提交审批结论后同一轮会回到 running，这里提前停表就丢掉了后续滚动
      if (status === 'paused') return;
      if (status !== 'running') scrollToBottom();
      stop();
    },
    { deep: false },
  );
}

async function handleStop() {
  const run = lastRun();
  if (!run) return;
  try {
    // 前端断开不等于后台停下，跳过这一步会让工作流继续跑完
    // 取消要带本轮的 API Key：网关按 Key 的归属校验执行权限
    await chatExecution.cancelRun(run);
  } catch {
    // 已经结束的执行，取消会报错，不影响本地收流
  }
  chatExecution.abort(run);
}

// ==================== 审批与再提交 ====================

const approvalSubmitting = ref(false);

function lastRun(): ChatRun | null {
  return messages.value.toReversed().find((item) => item.run)?.run || null;
}

/** 本轮已收尾（可 RETRY）：非 running、非 paused 的状态都是后端认可的终态 */
function isSettled(run: ChatRun): boolean {
  return run.status !== 'running' && run.status !== 'paused';
}

async function handleApprovalSubmit(
  msg: ChatMessage,
  decisions: ApprovalDecisionReq[],
) {
  const run = msg.run;
  if (!run || approvalSubmitting.value) return;
  approvalSubmitting.value = true;
  try {
    await chatExecution.submitApproval(run, decisions);
    message.success('审批结论已提交，继续执行');
    scrollToBottom();
  } catch (error: any) {
    message.error(error?.message || '提交审批结论失败');
  } finally {
    approvalSubmitting.value = false;
  }
}

async function handleRetry(run: ChatRun) {
  if (approvalSubmitting.value) return;
  approvalSubmitting.value = true;
  try {
    // 不传 inputs：后端沿用执行行里记录的上轮入参
    await chatExecution.retryRun(run);
    message.success('已按原入参重新提交');
    scrollToBottom();
  } catch (error: any) {
    message.error(error?.message || '重新提交失败');
  } finally {
    approvalSubmitting.value = false;
  }
}

function handleClear() {
  if (running.value) {
    message.warning('执行中，请先停止');
    return;
  }
  messages.value = [];
  stepsRun.value = null;
  formRef.value?.resetFields?.();
}

function toggleFull() {
  isFull.value = !isFull.value;
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
    paused: '待审批',
    running: '执行中',
    success: '已完成',
  };
  return map[run.status] || run.status;
}

// ==================== 快捷键 ====================

/**
 * Ctrl / ⌘ + Enter 发送，Esc 退全屏。
 * 监听挂在 window 上而不是某个输入框上：入参可能是文本、下拉、数字框甚至文件按钮，
 * 逐个绑定会漏掉焦点在消息区时的场景。
 * 退全屏走捕获阶段：Modal 里面包着 FocusLock，它在冒泡阶段就会把 ESC 吃掉（还可能 stopPropagation），
 * 等到 bubble 再到 window 时已经没机会了。
 */
function handleShortcut(event: KeyboardEvent) {
  if (!props.open) return;
  if (event.key === 'Escape') {
    // 执行过程弹窗还开着时，ESC 先交给它自己关；全屏下 :keyboard 已关，ESC 不会顺手关掉整个弹窗
    if (isFull.value && !stepsOpen.value) {
      isFull.value = false;
      event.stopPropagation();
    }
    return;
  }
  if (running.value) return;
  if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
  // 中文输入法确认候选词时同样会按下 Enter，别把选词当成发送
  if (event.isComposing) return;
  event.preventDefault();
  handleSend();
}

onMounted(() => window.addEventListener('keydown', handleShortcut, true));
onBeforeUnmount(() =>
  window.removeEventListener('keydown', handleShortcut, true),
);
</script>

<template>
  <a-modal
    :closable="false"
    :footer="null"
    :keyboard="!isFull"
    :open="props.open"
    :width="isFull ? '100vw' : 860"
    :wrap-class-name="isFull ? 'wf-chat-modal-full' : undefined"
    @update:open="close"
  >
    <!-- 标题行自带全屏/清空/关闭：同一个 flex 行，按钮必然水平对齐 -->
    <template #title>
      <div class="chat-title">
        <span class="title-text">
          {{ props.workflow?.name || '工作流对话' }}
        </span>
        <a-tag v-if="props.apiKey" color="blue">
          <KeyOutlined /> {{ props.apiKey.name }}
        </a-tag>
        <div class="title-actions">
          <Tooltip :title="isFull ? '退出全屏（Esc）' : '全屏'">
            <button class="head-btn" type="button" @click="toggleFull">
              <FullscreenExitOutlined v-if="isFull" />
              <FullscreenOutlined v-else />
            </button>
          </Tooltip>
          <Tooltip title="清空对话">
            <button class="head-btn" type="button" @click="handleClear">
              <DeleteOutlined />
            </button>
          </Tooltip>
          <Tooltip title="关闭">
            <button
              class="head-btn head-btn-close"
              type="button"
              @click="close(false)"
            >
              <CloseCircleFilled />
            </button>
          </Tooltip>
        </div>
      </div>
    </template>

    <a-spin :spinning="loading">
      <div
        ref="bodyRef"
        class="chat-body"
        :class="{ 'chat-body-full': isFull }"
      >
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

        <!-- 无头像：用户靠右、智能体靠左 -->
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="chat-row"
          :class="[msg.role]"
        >
          <div class="bubble">
            <!-- 用户：本次入参 -->
            <template v-if="msg.role === 'user'">
              <div class="bubble-time">{{ msg.time }}</div>
              <div
                v-if="inputEntries(msg.inputs || {}).length === 0"
                class="bubble-line"
              >
                （无入参）
              </div>
              <div
                v-for="entry in inputEntries(msg.inputs || {})"
                :key="entry.label"
                class="input-line"
              >
                <span class="input-label">{{ entry.label }}</span>
                <span class="input-value">{{ entry.value }}</span>
              </div>
            </template>

            <!-- 助手：各节点输出数据自动铺开 -->
            <template v-else-if="msg.run">
              <div class="bubble-time">{{ msg.time }}</div>
              <div class="run-bar">
                <LoadingOutlined v-if="msg.run.status === 'running'" spin />
                <span class="run-state">{{ runStateText(msg.run) }}</span>
                <span class="run-cost">{{
                  formatDuration(msg.run.duration)
                }}</span>
              </div>

              <div
                v-for="block in dataBlocks(msg.run)"
                :key="block.key"
                class="data-block"
              >
                <div class="block-head">
                  <span class="block-dot" :class="[block.status]"></span>
                  <span class="block-title">{{ block.title }}</span>
                  <span v-if="block.duration !== undefined" class="block-cost">
                    {{ formatDuration(block.duration) }}
                  </span>
                  <Tooltip title="复制本段数据">
                    <a class="block-copy" @click="copyBlock(block)">
                      <CopyOutlined />
                    </a>
                  </Tooltip>
                </div>

                <a-alert
                  v-if="block.error"
                  :message="block.error"
                  class="block-error"
                  type="error"
                />

                <!-- 思维链默认折叠，只留一行摘要，展开才渲染全文 -->
                <div v-if="block.reasoning" class="block-reason">
                  <div class="block-reason-head" @click="toggleReason(block)">
                    <component
                      :is="isReasonOpen(block) ? DownOutlined : RightOutlined"
                      class="reason-arrow"
                    />
                    <span class="block-reason-title">思考过程</span>
                    <span class="reason-brief">{{ reasonBrief(block) }}</span>
                  </div>
                  <div v-show="isReasonOpen(block)" class="block-reason-body">
                    <MarkdownRenderer :content="block.reasoning" />
                  </div>
                </div>

                <div v-if="block.answer" class="block-answer">
                  <MarkdownRenderer :content="block.answer" />
                  <span v-if="block.running" class="typing"></span>
                </div>

                <!-- 媒体/文件/向量/JSON 由 OutputValue 适配，复制按钮交给块头统一提供 -->
                <OutputValue
                  v-if="hasData(block.output)"
                  :show-copy="false"
                  :value="block.output"
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
                  dataBlocks(msg.run).length === 0
                "
                class="bubble-line placeholder"
              >
                正在排队执行…
              </div>

              <!-- 审批入口：本轮因待决审批节点停下时，直接在气泡里给出结论 -->
              <ApprovalPanel
                v-if="
                  msg.run.status === 'paused' && msg.run.awaiting.length > 0
                "
                :contexts="msg.run.awaiting"
                :submitting="approvalSubmitting"
                @submit="(decisions) => handleApprovalSubmit(msg, decisions)"
              />

              <!-- 执行过程入口：右下角蓝色高亮，点开弹窗逐级下钻输入 / 输出 -->
              <div v-if="msg.run.steps.length > 0" class="bubble-foot">
                <a class="steps-btn" @click="openSteps(msg.run)">
                  <PartitionOutlined />
                  执行过程 · {{ msg.run.steps.length }} 段
                </a>
                <a
                  v-if="isSettled(msg.run)"
                  class="steps-btn steps-btn-ghost"
                  @click="handleRetry(msg.run)"
                >
                  <RedoOutlined />
                  按原入参重跑
                </a>
              </div>
            </template>
          </div>
        </div>
      </div>
    </a-spin>

    <!-- 输入区：START 节点全部入参 + 文件上传 -->
    <div class="chat-composer">
      <div
        v-if="fields.length > 0"
        class="composer-form"
        :class="{ 'composer-form-full': isFull }"
      >
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
        <a-tooltip
          v-else-if="lastRun()?.status === 'paused'"
          title="取消本轮执行并结束挂起（待审批状态下同样生效）"
        >
          <a-button size="small" @click="handleStop">取消挂起</a-button>
        </a-tooltip>
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

    <!-- 执行过程明细：总览 → 输入/输出 → 展开数据 -->
    <ExecutionStepsModal v-model:open="stepsOpen" :run="stepsRun" />
  </a-modal>
</template>

<style lang="less" scoped>
.chat-title {
  display: flex;
  gap: 10px;
  align-items: center;

  .title-text {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    font-size: 15px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  // 关闭按钮由标题行自己管，和清空同一行，不再用弹窗默认右上角绝对定位的 X
  .title-actions {
    display: flex;
    flex-shrink: 0;
    gap: 6px;
    align-items: center;
    margin-left: auto;
  }
}

.head-btn {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  font-size: 14px;
  line-height: 1;
  color: #8c8c8c;
  background: transparent;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #1890ff;
    background: #f0f5ff;
  }

  // 关闭和清空同尺寸同行，只把 hover 换成暖色区分语义
  &.head-btn-close {
    font-size: 16px;

    &:hover {
      color: #ff4d4f;
      background: #fff1f0;
    }
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

  .bubble {
    flex: 0 1 auto;
    min-width: 0;
    max-width: 88%;
    padding: 10px 12px;
    overflow: hidden;
    overflow-wrap: anywhere;
    border: 1px solid #f0f0f0;
    border-radius: 10px;

    // 长地址、代码块、媒体一律收在气泡内
    :deep(pre) {
      max-width: 100%;
      overflow-x: auto;
    }

    :deep(img),
    :deep(video) {
      max-width: 100%;
    }
  }

  // 用户问题：靠右
  &.user {
    justify-content: flex-end;

    .bubble {
      color: #17415e;
      text-align: right;
      background: #e6f4ff;
      border-color: #bae0ff;
      border-bottom-right-radius: 3px;
    }
  }

  // 智能体回复：靠左
  &.assistant {
    justify-content: flex-start;

    .bubble {
      background: #fafafa;
      border-bottom-left-radius: 3px;
    }
  }
}

.bubble-time {
  margin-bottom: 4px;
  font-size: 11px;
  color: #bfbfbf;
}

.bubble-line {
  font-size: 13px;
  color: #595959;

  &.placeholder {
    color: #bfbfbf;
  }
}

.input-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  justify-content: flex-end;
  font-size: 13px;
  line-height: 22px;

  .input-label {
    font-size: 12px;
    color: #8c8c8c;
  }

  .input-value {
    color: #17415e;
    overflow-wrap: anywhere;
  }
}

.run-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  color: #8c8c8c;

  .run-state {
    font-weight: 500;
    color: #595959;
  }
}

// 节点数据块：块与块之间虚线分隔，各自一个复制按钮
.data-block {
  padding: 8px 0;

  & + .data-block {
    border-top: 1px dashed #d9d9d9;
  }

  .block-head {
    display: flex;
    gap: 6px;
    align-items: center;
    margin-bottom: 6px;
    font-size: 12px;

    .block-dot {
      width: 6px;
      height: 6px;
      background: #d9d9d9;
      border-radius: 50%;

      &.running {
        background: #1890ff;
      }

      &.completed {
        background: #52c41a;
      }

      &.failed {
        background: #ff4d4f;
      }

      &.timeout {
        background: #faad14;
      }

      &.cancelled {
        background: #bfbfbf;
      }

      // 审批挂起（等人工决策）与本轮沿用完成（skip）都要和正常跑完区分开
      &.awaiting,
      &.paused {
        background: #722ed1;
      }

      &.skipped {
        background: #13c2c2;
      }
    }

    .block-title {
      font-weight: 500;
      color: #262626;
    }

    .block-cost {
      color: #bfbfbf;
    }

    .block-copy {
      margin-left: auto;
      font-size: 12px;
      color: #bfbfbf;
      cursor: pointer;

      &:hover {
        color: #1890ff;
      }
    }
  }

  .block-error {
    margin-bottom: 6px;
  }

  .block-reason {
    padding: 8px 10px;
    margin-bottom: 6px;
    font-size: 12px;
    color: #8c8c8c;
    background: #fffbe6;
    border: 1px solid #ffe58f;
    border-radius: 8px;

    // 整行可点，折叠/展开不用瞄准小箭头
    .block-reason-head {
      display: flex;
      gap: 6px;
      align-items: center;
      cursor: pointer;
      user-select: none;
    }

    .reason-arrow {
      font-size: 10px;
      color: #bfbfbf;
    }

    .block-reason-title {
      font-weight: 500;
      color: #ad8b00;
    }

    .reason-brief {
      margin-left: auto;
      font-size: 11px;
      color: #bfbfbf;
    }

    .block-reason-body {
      padding-top: 6px;
      margin-top: 6px;
      border-top: 1px dashed #ffe58f;
    }
  }

  .block-answer {
    font-size: 13px;
    color: #262626;
  }
}

.bubble-foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 10px;

  // 蓝色高亮的执行过程入口
  .steps-btn {
    display: inline-flex;
    gap: 5px;
    align-items: center;
    padding: 3px 12px;
    font-size: 12px;
    color: #fff;
    background: #1890ff;
    border-radius: 12px;
    cursor: pointer;
    transition: background-color 0.2s;

    &:hover {
      color: #fff;
      background: #40a9ff;
    }

    // 次要动作（重跑）：不抢「执行过程」的视觉权重
    &.steps-btn-ghost {
      color: #1890ff;
      background: #fff;
      border: 1px solid #91d5ff;

      &:hover {
        color: #096dd9;
        background: #e6f7ff;
      }
    }
  }
}

.run-error {
  margin-top: 8px;
}

.typing {
  display: inline-block;
  width: 6px;
  height: 14px;
  margin-left: 2px;
  vertical-align: middle;
  background: #1890ff;
  animation: blink 1s steps(2) infinite;
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

<style lang="less">
/**
 * 全屏展示。
 *
 * Modal 默认 teleport 到 body，内容里的 .ant-modal-* 节点拿不到本组件的 scoped 属性，
 * 所以样式只能开在全局块里，靠 wrap-class-name 限定在本弹窗；
 * 高度一律用 flex 传递，避开“入参表单多高算多高”的硬编码 calc。
 */
.wf-chat-modal-full {
  .ant-modal {
    top: 0;
    max-width: 100vw;
    margin: 0;
    padding-bottom: 0;
  }

  .ant-modal-content {
    display: flex;
    flex-direction: column;
    height: 100vh;
    border-radius: 0;
  }

  .ant-modal-header {
    flex: none;
  }

  .ant-modal-body {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }

  // a-spin 包了两层容器，flex 不接着传下去消息区就不会撑开
  .ant-spin-nested-loading {
    display: flex;
    flex: 1;
    min-height: 0;

    > .ant-spin-container {
      display: flex;
      flex: 1;
      flex-direction: column;
      min-height: 0;
    }
  }

  .chat-body.chat-body-full {
    flex: 1;
    height: auto;
    min-height: 0;
  }

  .chat-composer {
    flex: none;
  }

  // 双类名是为了压过 scoped 里的 240px，不依赖样式注入顺序
  .chat-composer .composer-form.composer-form-full {
    max-height: 40vh;
  }
}
</style>
