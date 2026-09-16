<script setup lang="ts">
/**
 * 执行过程明细弹窗（对话气泡右下角「执行过程」入口打开）。
 *
 * 一次执行经过的每个节点铺在同一块画布里，节点之间用虚线分隔，输出按值形态解析：
 * 文本走 Markdown、向量给维度摘要、图片/视频/音频直链渲染成媒体并保留可复制的
 * 地址，识别不了一律退回 JSON 树（判定见 OutputValue，新增节点/模型类型无需改这里）。
 */
import type { ChatRun, ChatStep } from './useChatExecution';

import MarkdownRenderer from '../debug/MarkdownRenderer.vue';
import { formatDuration } from './chat-output';
import OutputValue from './OutputValue.vue';

const props = defineProps<{
  open: boolean;
  run?: ChatRun | null;
}>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

const statusText: Record<string, string> = {
  cancelled: '已跳过',
  completed: '完成',
  failed: '失败',
  paused: '等待中',
  running: '执行中',
  timeout: '已超时',
};

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

/** 节点有没有跑出可展示的数据（结构化输出 / 流式正文 / 思考链至少有一个） */
function hasData(step: ChatStep): boolean {
  return (
    (step.output !== undefined && step.output !== null) ||
    !!streamText(step) ||
    !!reasoningText(step)
  );
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="760"
    title="执行过程"
    @update:open="close"
  >
    <div class="steps-canvas">
      <div
        v-if="!props.run || props.run.steps.length === 0"
        class="steps-empty"
      >
        暂无节点执行记录
      </div>
      <div
        v-for="step in props.run?.steps || []"
        :key="step.nodeId"
        class="step-block"
      >
        <div class="block-head">
          <span class="step-dot" :class="[step.status]"></span>
          <span class="step-label">{{ step.label }}</span>
          <span v-if="step.nodeType" class="step-type">{{
            step.nodeType
          }}</span>
          <span class="step-status">{{ statusText[step.status] }}</span>
          <span class="step-cost">{{ formatDuration(step.duration) }}</span>
        </div>

        <a-alert
          v-if="step.error"
          :message="step.error"
          class="block-error"
          type="error"
        />

        <div v-if="reasoningText(step)" class="block-reason">
          <div class="block-reason-title">思考过程</div>
          <MarkdownRenderer :content="reasoningText(step)" />
        </div>

        <div v-if="streamText(step)" class="block-answer">
          <MarkdownRenderer :content="streamText(step)" />
        </div>

        <OutputValue
          v-if="step.output !== undefined && step.output !== null"
          :value="step.output"
        />

        <div v-if="!step.error && !hasData(step)" class="block-empty">
          {{ step.status === 'running' ? '节点执行中…' : '（无输出数据）' }}
        </div>
      </div>
    </div>
  </a-modal>
</template>

<style lang="less" scoped>
.steps-canvas {
  max-height: 60vh;
  padding: 4px 8px 12px 0;
  overflow-y: auto;
}

.steps-empty {
  padding: 40px 0;
  font-size: 13px;
  color: #bfbfbf;
  text-align: center;
}

.step-block {
  padding: 10px 0;

  // 节点之间用虚线分隔（首个不加，避免与弹窗头撞线）
  & + .step-block {
    border-top: 1px dashed #e8e8e8;
  }
}

.block-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;

  .step-dot {
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
    margin-left: auto;
    color: #bfbfbf;
  }
}

.block-error {
  margin-bottom: 8px;
}

.block-reason {
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #8c8c8c;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 8px;

  .block-reason-title {
    margin-bottom: 4px;
    font-weight: 500;
    color: #ad8b00;
  }
}

.block-answer {
  padding-bottom: 6px;
  font-size: 13px;
  color: #262626;
  overflow-wrap: anywhere;
}

.block-empty {
  font-size: 12px;
  color: #bfbfbf;
}
</style>
