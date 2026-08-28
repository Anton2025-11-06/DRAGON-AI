<script setup lang="ts">
import { computed } from 'vue';

export type NodeStatus =
  | 'completed'
  | 'failed'
  | 'pending'
  | 'running'
  | 'skipped'
  | 'waiting'
  | null;

interface Props {
  status: NodeStatus;
  duration?: number;
  /** 是否使用英文标签 */
  english?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  english: true,
});

const statusText = computed(() => {
  const labelsEN: Record<string, string> = {
    pending: 'Waiting',
    waiting: 'Waiting',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    skipped: 'Skipped',
  };
  const labelsCN: Record<string, string> = {
    pending: '等待中',
    waiting: '等待中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过',
  };
  const labels = props.english ? labelsEN : labelsCN;
  return props.status ? labels[props.status] || '' : '';
});

const durationText = computed(() => {
  if (props.duration === undefined || props.status !== 'completed') return '';
  return props.duration < 1000
    ? `${(props.duration / 1000).toFixed(3)}s`
    : `${(props.duration / 1000).toFixed(2)}s`;
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
