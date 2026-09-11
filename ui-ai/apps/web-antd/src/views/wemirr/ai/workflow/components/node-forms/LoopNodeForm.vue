<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="最大迭代次数" required>
      <a-input-number
        v-model:value="formData.maxIterations"
        :min="1"
        :max="1000"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="form-hint">防止无限循环，达到此次数后强制退出</div>
    </a-form-item>

    <a-form-item label="退出条件表达式">
      <a-input
        v-model:value="formData.exitCondition"
        :placeholder="'如: {{result}} == \'done\' 或 {{count}} >= 10'"
        @change="handleChange"
      />
      <div class="form-hint">当表达式为 true 时退出循环</div>
    </a-form-item>

    <a-form-item label="循环变量名">
      <a-input
        v-model:value="formData.loopVariable"
        placeholder="默认: loop_index"
        @change="handleChange"
      />
      <div class="form-hint">存储当前迭代次数的变量名</div>
    </a-form-item>

    <a-alert type="info" show-icon class="loop-help">
      <template #message>循环节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>循环节点会重复执行其后续连接的节点</li>
          <li>从 <strong>循环出口</strong> 连接的节点会被重复执行</li>
          <li>从 <strong>退出出口</strong> 连接的节点在循环结束后执行</li>
          <li>
            可通过 <code v-pre>{{ loop_index }}</code> 获取当前迭代次数
          </li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * Loop 节点配置表单
 * 配置循环执行参数
 */
import type { LoopNodeConfig } from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

// Props
interface Props {
  config: LoopNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: LoopNodeConfig): void;
}>();

// 表单数据
const formData = reactive<LoopNodeConfig>({
  maxIterations: 10,
  exitCondition: '',
  loopVariable: '',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      maxIterations: config.maxIterations ?? 10,
      exitCondition: config.exitCondition || '',
      loopVariable: config.loopVariable || '',
    });
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
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .loop-help {
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

      code {
        padding: 1px 4px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 2px;
      }
    }
  }
}
</style>
