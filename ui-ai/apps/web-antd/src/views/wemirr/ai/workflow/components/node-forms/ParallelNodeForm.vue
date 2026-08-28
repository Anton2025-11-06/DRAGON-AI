<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="等待策略">
      <a-radio-group
        v-model:value="formData.waitStrategy"
        @change="handleChange"
      >
        <a-radio-button value="ALL">等待全部完成</a-radio-button>
        <a-radio-button value="ANY">任一完成即可</a-radio-button>
      </a-radio-group>
      <div class="form-hint">
        <template v-if="formData.waitStrategy === 'ALL'">
          所有并行分支都执行完成后才继续
        </template>
        <template v-else>
          任意一个分支完成后立即继续，其他分支会被取消
        </template>
      </div>
    </a-form-item>

    <a-alert type="info" show-icon class="parallel-help">
      <template #message>并行节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>并行节点可以同时执行多个分支</li>
          <li>从并行节点的多个输出端口连接不同的节点</li>
          <li>每个输出端口代表一个并行分支</li>
          <li>所有分支的输出会合并到一个对象中</li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * Parallel 节点配置表单
 * 配置并行执行参数
 */
import type { ParallelNodeConfig } from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

// Props
interface Props {
  config: ParallelNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ParallelNodeConfig): void;
}>();

// 表单数据
const formData = reactive<ParallelNodeConfig>({
  waitStrategy: 'ALL',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    formData.waitStrategy = config.waitStrategy || 'ALL';
  },
  { immediate: true, deep: true },
);

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  .form-hint {
    margin-top: 8px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .parallel-help {
    margin-top: 8px;

    .help-list {
      padding-left: 16px;
      margin: 0;
      font-size: 12px;

      li {
        margin-bottom: 4px;

        &:last-child {
          margin-bottom: 0;
        }
      }
    }
  }
}
</style>
