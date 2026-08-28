<script setup lang="ts">
/**
 * 迭代节点配置表单
 * 对数组元素进行批量处理，支持顺序和并行模式
 */
import type {
  IterationNodeConfig,
  ProcessingMode,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import { ApartmentOutlined, OrderedListOutlined } from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: IterationNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: IterationNodeConfig): void;
}>();

// 表单数据
const formData = reactive<IterationNodeConfig>({
  arrayVariable: '',
  processingMode: 'SEQUENTIAL' as ProcessingMode,
  parallelCount: undefined,
  iterationTimeout: undefined,
  maxIterations: undefined,
  outputVariable: 'results',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      arrayVariable: config.arrayVariable || '',
      processingMode: config.processingMode || 'SEQUENTIAL',
      parallelCount: config.parallelCount,
      iterationTimeout: config.iterationTimeout,
      maxIterations: config.maxIterations,
      outputVariable: config.outputVariable || 'results',
    });
  },
  { immediate: true, deep: true },
);

// 处理配置变更
function handleChange() {
  const config: IterationNodeConfig = {
    arrayVariable: formData.arrayVariable,
    processingMode: formData.processingMode,
    parallelCount:
      formData.processingMode === 'PARALLEL'
        ? formData.parallelCount
        : undefined,
    iterationTimeout: formData.iterationTimeout,
    maxIterations: formData.maxIterations,
    outputVariable: formData.outputVariable,
  };
  emit('update:config', config);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 数组变量选择 -->
    <a-form-item label="迭代数组" required>
      <VariableInput
        v-model="formData.arrayVariable"
        :current-node-id="nodeId"
        placeholder="选择要迭代的数组变量"
        :filter-types="[
          'array',
          'Array[string]',
          'Array[number]',
          'Array[File]',
        ]"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 处理模式 -->
    <a-form-item label="处理模式">
      <a-radio-group
        v-model:value="formData.processingMode"
        button-style="solid"
        @change="handleChange"
      >
        <a-radio-button value="SEQUENTIAL">
          <OrderedListOutlined /> 顺序处理
        </a-radio-button>
        <a-radio-button value="PARALLEL">
          <ApartmentOutlined /> 并行处理
        </a-radio-button>
      </a-radio-group>
      <div class="form-hint">
        {{
          formData.processingMode === 'SEQUENTIAL'
            ? '按顺序逐个处理数组元素'
            : '同时处理多个数组元素，提高效率'
        }}
      </div>
    </a-form-item>

    <!-- 并行数量（仅并行模式显示） -->
    <a-form-item v-if="formData.processingMode === 'PARALLEL'" label="并行数量">
      <a-input-number
        v-model:value="formData.parallelCount"
        :min="1"
        :max="20"
        placeholder="默认为 CPU 核心数"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="form-hint">同时处理的最大元素数量，建议不超过 10</div>
    </a-form-item>

    <!-- 高级设置 -->
    <a-collapse ghost>
      <a-collapse-panel key="advanced" header="高级设置">
        <!-- 单次迭代超时 -->
        <a-form-item label="单次迭代超时 (毫秒)">
          <a-input-number
            v-model:value="formData.iterationTimeout"
            :min="1000"
            :max="300000"
            :step="1000"
            placeholder="默认 30000"
            style="width: 100%"
            @change="handleChange"
          />
          <div class="form-hint">
            单个元素处理的最大时间，超时将按错误处理策略处理
          </div>
        </a-form-item>

        <!-- 最大迭代次数 -->
        <a-form-item label="最大迭代次数">
          <a-input-number
            v-model:value="formData.maxIterations"
            :min="1"
            :max="10000"
            placeholder="默认 1000"
            style="width: 100%"
            @change="handleChange"
          />
          <div class="form-hint">限制最大处理元素数量，防止无限循环</div>
        </a-form-item>

        <!-- 输出变量名 -->
        <a-form-item label="输出变量名">
          <a-input
            v-model:value="formData.outputVariable"
            placeholder="results"
            @change="handleChange"
          />
          <div class="form-hint">存储迭代结果数组的变量名</div>
        </a-form-item>
      </a-collapse-panel>
    </a-collapse>

    <!-- 内置变量说明 -->
    <a-alert type="info" show-icon class="builtin-vars-info">
      <template #message>内置变量</template>
      <template #description>
        <div class="builtin-vars">
          <div class="var-item">
            <code>item</code>
            <span>当前迭代的元素</span>
          </div>
          <div class="var-item">
            <code>index</code>
            <span>当前元素的索引（从 0 开始）</span>
          </div>
        </div>
        <div class="usage-hint">
          在迭代体内的节点中使用 <code v-pre>{{ item }}</code> 和
          <code v-pre>{{ index }}</code> 引用
        </div>
      </template>
    </a-alert>
  </a-form>
</template>

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

  :deep(.ant-collapse-header) {
    padding: 8px 0 !important;
    font-size: 12px;
    color: #595959;
  }

  :deep(.ant-collapse-content-box) {
    padding: 0 !important;
  }

  .builtin-vars-info {
    margin-top: 16px;

    .builtin-vars {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 8px;

      .var-item {
        display: flex;
        align-items: center;
        gap: 8px;

        code {
          padding: 2px 6px;
          font-family: monospace;
          font-size: 12px;
          background-color: #f5f5f5;
          border-radius: 3px;
        }

        span {
          font-size: 12px;
          color: #595959;
        }
      }
    }

    .usage-hint {
      font-size: 11px;
      color: #8c8c8c;

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
