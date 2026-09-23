<script setup lang="ts">
/**
 * 执行过程弹窗（对话气泡右下角「执行过程」入口打开）。
 *
 * 三级下钻，逐层只多暴露一层信息，避免一屏铺开几十段 JSON：
 * 1. 总览：本次执行经过的每一段（每个节点）一行，只给节点名称 + 耗时；
 * 2. 点开某一段：出现该段的「输入 / 输出」两个入口；
 * 3. 点开其中一个：才展开具体数据。
 * 数据一律交给 OutputValue 按值形态渲染（文本/媒体/文件/向量/JSON），
 * 新增节点类型或模型能力类型时不需要改这里。
 */
import type { ChatRun, ChatStep } from './useChatExecution';

import { ref, watch } from 'vue';

import {
  DownOutlined,
  FieldTimeOutlined,
  RightOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import MarkdownRenderer from '../debug/MarkdownRenderer.vue';
import { formatDuration, toolCallState, toolKindText } from './chat-output';
import OutputValue from './OutputValue.vue';

const props = defineProps<{
  open: boolean;
  run?: ChatRun | null;
}>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

const statusText: Record<string, string> = {
  // awaiting = 审批节点挂起等人工决策；skipped = 恢复提交里沿用上轮结果未重跑
  awaiting: '待审批',
  cancelled: '已取消',
  completed: '完成',
  failed: '失败',
  paused: '已挂起',
  running: '执行中',
  skipped: '本轮沿用',
  timeout: '已超时',
};

/** 第二段：当前展开到哪一段执行（同时只展开一段，看板上更像时间轴） */
const activeNodeId = ref<null | string>(null);
/** 第三段：`${nodeId}:${part}` → 是否展开具体数据 */
const openParts = ref<Record<string, boolean>>({});

// 换一次执行（点了另一条气泡的入口）不能沿用上一段的展开位置
watch(
  () => [props.open, props.run?.executionId] as const,
  ([open]) => {
    if (open) {
      activeNodeId.value = null;
      openParts.value = {};
    }
  },
);

function close(open: boolean) {
  emit('update:open', open);
}

/** 流式节点的 node.completed 不带 output（正文只走 node.delta），这里补回来 */
function streamText(step: ChatStep): string {
  return props.run?.streams?.[step.nodeId] || '';
}

function reasoningText(step: ChatStep): string {
  return props.run?.reasoning?.[step.nodeId] || '';
}

/** 有没有值得展开的数据：空对象/空数组展开只会给用户一个空白面板 */
function hasData(value: any): boolean {
  if (value === null || value === undefined || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function stepInput(step: ChatStep): any {
  return step.input;
}

/** 这一段的工具调用（只有配了工具的 LLM 节点会有） */
function toolCallsOf(step: ChatStep): NonNullable<ChatStep['toolCalls']> {
  return step.toolCalls || [];
}

/** 该段的输出：结构化 output 优先，流式节点退回累积正文 */
function stepOutput(step: ChatStep): any {
  if (hasData(step.output)) return step.output;
  const text = streamText(step);
  return text || undefined;
}

function isPartOpen(step: ChatStep, part: string): boolean {
  return !!openParts.value[partKey(step, part)];
}

function togglePart(step: ChatStep, part: string) {
  const key = partKey(step, part);
  openParts.value = { ...openParts.value, [key]: !openParts.value[key] };
}

function toggleStep(step: ChatStep) {
  activeNodeId.value = activeNodeId.value === step.nodeId ? null : step.nodeId;
  // 收起该段时一并收起它的输入/输出，重新点开回到总览口径
  if (activeNodeId.value !== step.nodeId) {
    for (const part of ['input', 'output', 'reason', 'tools']) {
      const key = partKey(step, part);
      if (openParts.value[key]) {
        openParts.value = { ...openParts.value, [key]: false };
      }
    }
  }
}

/** 没数据的入口不给展开，否则点开一片空白会被当成渲染坏了 */
function partData(step: ChatStep, part: string): any {
  if (part === 'input') return step.input;
  if (part === 'reason') return reasoningText(step);
  if (part === 'tools') return toolCallsOf(step);
  return stepOutput(step);
}

function onPartHeadClick(step: ChatStep, part: string) {
  if (!hasData(partData(step, part))) {
    message.info('该段暂无数据');
    return;
  }
  togglePart(step, part);
}

/** 总览表头文案：段数 + 总耗时 */
function summaryText(run?: ChatRun | null): string {
  if (!run || run.steps.length === 0) return '';
  const cost =
    run.duration ?? run.steps.reduce((sum, s) => sum + (s.duration || 0), 0);
  return `共 ${run.steps.length} 段 · 总耗时 ${formatDuration(cost)}`;
}

function partKey(step: ChatStep, part: string): string {
  return `${step.nodeId}:${part}`;
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="820"
    title="执行过程"
    @update:open="close"
  >
    <div class="steps-panel">
      <div v-if="props.run && props.run.steps.length > 0" class="steps-bar">
        <span class="bar-count">{{ summaryText(props.run) }}</span>
        <span class="bar-tip">点击某一段查看输入 / 输出</span>
      </div>

      <div class="steps-canvas">
        <div
          v-if="!props.run || props.run.steps.length === 0"
          class="steps-empty"
        >
          暂无节点执行记录
        </div>

        <div
          v-for="(step, index) in props.run?.steps || []"
          :key="step.nodeId"
          class="step-seg"
        >
          <!-- 一级：总览行 -->
          <div
            class="step-row"
            :class="{ active: activeNodeId === step.nodeId }"
            @click="toggleStep(step)"
          >
            <RightOutlined
              class="row-caret"
              :class="{ open: activeNodeId === step.nodeId }"
            />
            <span class="step-order">{{ index + 1 }}</span>
            <span class="step-dot" :class="[step.status]"></span>
            <span class="step-label">{{ step.label }}</span>
            <span v-if="step.nodeType" class="step-type">{{
              step.nodeType
            }}</span>
            <span
              v-if="toolCallsOf(step).length > 0"
              class="step-tools"
              title="该段调用过工具"
            >
              <ToolOutlined />
              {{ toolCallsOf(step).length }}
            </span>
            <span class="step-status">{{ statusText[step.status] }}</span>
            <span class="step-cost">
              <FieldTimeOutlined />
              {{ formatDuration(step.duration) }}
            </span>
          </div>

          <!-- 二级：该段的输入 / 输出入口 -->
          <div v-if="activeNodeId === step.nodeId" class="step-detail">
            <a-alert
              v-if="step.error"
              :message="step.error"
              class="detail-error"
              type="error"
            />

            <div class="io-group">
              <div class="io-item">
                <div class="io-head" @click="onPartHeadClick(step, 'input')">
                  <DownOutlined
                    v-if="isPartOpen(step, 'input')"
                    class="io-caret"
                  />
                  <RightOutlined v-else class="io-caret" />
                  <span class="io-name">输入</span>
                  <span class="io-desc">上游节点输出</span>
                </div>
                <div v-if="isPartOpen(step, 'input')" class="io-body">
                  <OutputValue
                    v-if="hasData(stepInput(step))"
                    :value="stepInput(step)"
                  />
                  <span v-else class="io-none">
                    该段无输入（起始节点或无引用上游）
                  </span>
                </div>
              </div>

              <div class="io-item">
                <div class="io-head" @click="onPartHeadClick(step, 'output')">
                  <DownOutlined
                    v-if="isPartOpen(step, 'output')"
                    class="io-caret"
                  />
                  <RightOutlined v-else class="io-caret" />
                  <span class="io-name">输出</span>
                  <span class="io-desc">{{
                    step.status === 'running' ? '执行中…' : '该段产出数据'
                  }}</span>
                </div>
                <div v-if="isPartOpen(step, 'output')" class="io-body">
                  <template v-if="hasData(stepOutput(step))">
                    <!-- 流式正文按 Markdown 读，结构化结果按值形态渲染 -->
                    <div
                      v-if="streamText(step) && !hasData(step.output)"
                      class="io-answer"
                    >
                      <MarkdownRenderer :content="streamText(step)" />
                    </div>
                    <OutputValue v-else :value="stepOutput(step)" />
                  </template>
                  <span v-else class="io-none">
                    {{
                      step.status === 'running'
                        ? '节点执行中，暂无输出'
                        : '该段无输出数据'
                    }}
                  </span>
                </div>
              </div>

              <div v-if="reasoningText(step)" class="io-item">
                <div class="io-head" @click="onPartHeadClick(step, 'reason')">
                  <DownOutlined
                    v-if="isPartOpen(step, 'reason')"
                    class="io-caret"
                  />
                  <RightOutlined v-else class="io-caret" />
                  <span class="io-name">思考过程</span>
                </div>
                <div v-if="isPartOpen(step, 'reason')" class="io-body">
                  <div class="io-reason">
                    <MarkdownRenderer :content="reasoningText(step)" />
                  </div>
                </div>
              </div>

              <!-- 工具调用过程：配了工具的 LLM 节点才会出现，不受「输出工具
                   结果」开关约束（那个开关只管结果要不要推成可见正文） -->
              <div v-if="toolCallsOf(step).length > 0" class="io-item">
                <div class="io-head" @click="onPartHeadClick(step, 'tools')">
                  <DownOutlined
                    v-if="isPartOpen(step, 'tools')"
                    class="io-caret"
                  />
                  <RightOutlined v-else class="io-caret" />
                  <span class="io-name">工具调用</span>
                  <span class="io-desc">
                    共 {{ toolCallsOf(step).length }} 次 · 结果为截断摘要
                  </span>
                </div>
                <div v-if="isPartOpen(step, 'tools')" class="io-body">
                  <div
                    v-for="(call, idx) in toolCallsOf(step)"
                    :key="call.toolCallId || idx"
                    class="tool-call"
                  >
                    <div class="tool-call-head">
                      <ToolOutlined class="tool-call-icon" />
                      <span class="tool-call-name">{{
                        call.toolName || '未命名工具'
                      }}</span>
                      <a-tag size="small">{{ toolKindText(call.kind) }}</a-tag>
                      <a-tag
                        :color="toolCallState(call, call.resumed).color"
                        size="small"
                      >
                        {{ toolCallState(call, call.resumed).text }}
                      </a-tag>
                      <span class="tool-call-round">
                        第 {{ call.round || '-' }} 轮
                      </span>
                    </div>
                    <div class="tool-call-line">
                      <span class="tool-call-label">入参</span>
                      <div class="tool-call-value">
                        <OutputValue :value="call.arguments || {}" />
                      </div>
                    </div>
                    <div class="tool-call-line">
                      <span class="tool-call-label">结果</span>
                      <pre v-if="call.result" class="tool-call-result">{{
                        call.result
                      }}</pre>
                      <span v-else class="tool-call-pending">
                        {{ call.error ? '无结果' : '等待返回…' }}
                      </span>
                    </div>
                    <div v-if="call.error" class="tool-call-error">
                      {{ call.error }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<style lang="less" scoped>
.steps-panel {
  display: flex;
  flex-direction: column;
}

.steps-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  padding-bottom: 8px;
  margin-bottom: 4px;
  border-bottom: 1px solid #f0f0f0;

  .bar-count {
    font-size: 12px;
    font-weight: 500;
    color: #262626;
  }

  .bar-tip {
    font-size: 12px;
    color: #bfbfbf;
  }
}

.steps-canvas {
  max-height: 58vh;
  overflow-y: auto;
}

.steps-empty {
  padding: 40px 0;
  font-size: 13px;
  color: #bfbfbf;
  text-align: center;
}

.step-seg {
  & + .step-seg {
    border-top: 1px dashed #e8e8e8;
  }
}

// 一级总览行：紧凑行高，一屏尽量多看几段
.step-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 9px 8px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 6px;
  transition: background-color 0.2s;

  &:hover {
    background: #f5f9ff;
  }

  &.active {
    background: #e6f4ff;
  }

  .row-caret {
    flex-shrink: 0;
    font-size: 10px;
    color: #bfbfbf;
    transition: transform 0.2s;

    &.open {
      transform: rotate(90deg);
    }
  }

  .step-order {
    min-width: 16px;
    font-size: 11px;
    color: #bfbfbf;
    text-align: right;
  }

  .step-dot {
    flex-shrink: 0;
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

    &.awaiting {
      background: #722ed1;
    }

    &.skipped {
      background: #13c2c2;
    }
  }

  .step-label {
    font-size: 13px;
    font-weight: 500;
    color: #262626;
  }

  .step-type {
    padding: 0 6px;
    font-size: 11px;
    color: #8c8c8c;
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 4px;
  }

  .step-status {
    color: #595959;
  }

  .step-cost {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    margin-left: auto;
    color: #8c8c8c;
  }
}

// 二级：输入 / 输出入口
.step-detail {
  padding: 2px 8px 10px 26px;
}

.detail-error {
  margin-bottom: 8px;
}

.io-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.io-item {
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  background: #fff;
}

.io-head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 7px 10px;
  cursor: pointer;

  &:hover {
    background: #fafafa;
  }

  .io-caret {
    font-size: 10px;
    color: #bfbfbf;
  }

  .io-name {
    font-size: 12px;
    font-weight: 500;
    color: #1890ff;
  }

  .io-desc {
    font-size: 11px;
    color: #bfbfbf;
  }
}

.io-body {
  padding: 8px 10px 10px 28px;
  border-top: 1px dashed #f0f0f0;
  overflow-wrap: anywhere;

  // 展开的数据收在面板内，长地址与代码块不越界
  :deep(pre) {
    max-width: 100%;
    overflow-x: auto;
  }

  :deep(img),
  :deep(video) {
    max-width: 100%;
  }
}

.io-none {
  font-size: 12px;
  color: #bfbfbf;
}

.io-answer {
  font-size: 13px;
  color: #262626;
  overflow-wrap: anywhere;
}

.io-reason {
  padding: 8px 10px;
  font-size: 12px;
  color: #8c8c8c;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 6px;
}

// 总览行的工具调用次数角标
.step-tools {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  font-size: 11px;
  color: #fa8c16;
}

// 调用详情：一次一块，紧凑堆叠（多轮多个调用时不至于把面板撑到看不清）
.tool-call {
  padding: 8px 0;

  & + .tool-call {
    margin-top: 8px;
    border-top: 1px dashed #f0f0f0;
  }
}

.tool-call-head {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;

  .tool-call-icon {
    font-size: 12px;
    color: #fa8c16;
  }

  .tool-call-name {
    font-size: 12px;
    font-weight: 500;
    color: #262626;
    overflow-wrap: anywhere;
  }

  .tool-call-round {
    font-size: 11px;
    color: #bfbfbf;
  }
}

.tool-call-line {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 4px;

  .tool-call-label {
    flex: none;
    padding-top: 1px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .tool-call-value {
    flex: 1;
    min-width: 0;
  }
}

.tool-call-result {
  max-height: 160px;
  padding: 6px 8px;
  margin: 0;
  overflow: auto;
  font-size: 12px;
  color: #434343;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  border-radius: 4px;
}

.tool-call-pending {
  font-size: 12px;
  font-style: italic;
  color: #bfbfbf;
}

.tool-call-error {
  margin-top: 4px;
  font-size: 11px;
  color: #ff4d4f;
  overflow-wrap: anywhere;
}
</style>
