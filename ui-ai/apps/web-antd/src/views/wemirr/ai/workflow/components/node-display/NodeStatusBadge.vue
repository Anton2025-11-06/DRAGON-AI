<script setup lang="ts">
import { computed } from 'vue';

export type NodeStatus =
  | 'awaiting'
  | 'cancelled'
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'skipped'
  | 'timeout'
  | 'waiting'
  | null;

interface Props {
  status: NodeStatus;
  duration?: number;
  /** 是否使用英文标签 */
  english?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  duration: undefined,
  english: true,
});

const statusText = computed(() => {
  const labelsEN: Record<string, string> = {
    awaiting: 'Awaiting approval',
    cancelled: 'Cancelled',
    pending: 'Waiting',
    waiting: 'Waiting',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    skipped: 'Skipped',
    timeout: 'Timeout',
  };
  const labelsCN: Record<string, string> = {
    awaiting: '待审批',
    cancelled: '已取消',
    pending: '等待中',
    waiting: '等待中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过',
    timeout: '已超时',
  };
  const labels = props.english ? labelsEN : labelsCN;
  return props.status ? labels[props.status] || '' : '';
});

const durationText = computed(() => {
  // 超时/取消的分支也带真实耗时，一并展示便于判断卡在哪个分支
  if (
    props.duration === undefined ||
    !['cancelled', 'completed', 'timeout'].includes(props.status || '')
  ) {
    return '';
  }
  return props.duration < 1000
    ? `${Math.round(props.duration)}ms`
    : `${(props.duration / 1000).toFixed(1)}s`;
});
</script>

<template>
  <div v-if="status" class="node-status-badge" :class="[status]">
    <span class="status-dot"></span>
    <span class="status-text">{{ statusText }}</span>
    <span v-if="durationText" class="duration-text">{{ durationText }}</span>
  </div>
</template>

<style scoped lang="less">
.node-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 10px;
  border-radius: 10px;

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }

  .duration-text {
    margin-left: 2px;
    color: #8c8c8c;
  }

  &.waiting {
    background: #f5f5f5;
    border: 1px solid #d9d9d9;
    color: #8c8c8c;
    .status-dot {
      background: #8c8c8c;
    }
  }

  &.running {
    background: #e6f7ff;
    border: 1px solid #91d5ff;
    color: #1890ff;
    .status-dot {
      background: #1890ff;
      animation: pulse 1s infinite;
    }
  }

  &.completed {
    background: #f6ffed;
    border: 1px solid #b7eb8f;
    color: #52c41a;
    .status-dot {
      background: #52c41a;
    }
  }

  &.failed {
    background: #fff2f0;
    border: 1px solid #ffccc7;
    color: #ff4d4f;
    .status-dot {
      background: #ff4d4f;
    }
  }

  &.skipped {
    background: #f5f5f5;
    border: 1px solid #d9d9d9;
    color: #8c8c8c;
    .status-dot {
      background: #8c8c8c;
    }
  }

  &.pending {
    background: #f5f5f5;
    border: 1px solid #d9d9d9;
    color: #8c8c8c;
    .status-dot {
      background: #8c8c8c;
    }
  }

  // 审批节点挂起：紫色等待态，与节点追踪面板同一语义
  &.awaiting {
    background: #f9f0ff;
    border: 1px solid #d3adf7;
    color: #722ed1;
    .status-dot {
      background: #722ed1;
      animation: pulse 1.4s infinite;
    }
  }

  // 并行分支等待超时：黄色告警态，区别于蓝色执行中
  &.timeout {
    background: #fffbe6;
    border: 1px solid #ffe58f;
    color: #faad14;
    .status-dot {
      background: #faad14;
    }
  }

  // 并行分支被其他分支先完成短路
  &.cancelled {
    background: #f5f5f5;
    border: 1px dashed #d9d9d9;
    color: #8c8c8c;
    .status-dot {
      background: #bfbfbf;
    }
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}
</style>
