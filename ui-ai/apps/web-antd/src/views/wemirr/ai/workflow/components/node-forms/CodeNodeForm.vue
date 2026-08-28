<script setup lang="ts">
/**
 * Code 节点配置表单
 * 配置代码执行参数
 */
import type { CodeNodeConfig } from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

// Props
interface Props {
  config: CodeNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: CodeNodeConfig): void;
}>();

// 表单数据
const formData = reactive<CodeNodeConfig>({
  language: 'JAVASCRIPT',
  code: '',
  outputVariable: '',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      language: config.language || 'JAVASCRIPT',
      code: config.code || '',
      outputVariable: config.outputVariable || '',
    });
  },
  { immediate: true, deep: true },
);

// 获取代码占位符
function getCodePlaceholder(): string {
  if (formData.language === 'JAVASCRIPT') {
    return `// 通过 inputs 访问上游数据
// 示例:
const data = inputs.llm_output;
const result = data.toUpperCase();
return result;`;
  }
  return `# 通过 inputs 访问上游数据
# 示例:
data = inputs['llm_output']
result = data.upper()
return result`;
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="编程语言">
      <a-radio-group v-model:value="formData.language" @change="handleChange">
        <a-radio-button value="JAVASCRIPT">JavaScript</a-radio-button>
        <a-radio-button value="PYTHON">Python</a-radio-button>
      </a-radio-group>
    </a-form-item>

    <a-form-item label="代码" required>
      <a-textarea
        v-model:value="formData.code"
        :rows="12"
        :placeholder="getCodePlaceholder()"
        class="code-editor"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: code_output"
        @change="handleChange"
      />
    </a-form-item>

    <a-alert type="info" show-icon class="code-help">
      <template #message>代码节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>通过 <code>inputs</code> 对象访问上游节点的输出</li>
          <li>代码必须返回一个值作为节点输出</li>
          <li>
            <template v-if="formData.language === 'JAVASCRIPT'">
              JavaScript 示例: <code>return inputs.data.toUpperCase();</code>
            </template>
            <template v-else>
              Python 示例: <code>return inputs['data'].upper()</code>
            </template>
          </li>
          <li>支持常用的内置函数和库</li>
        </ul>
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

  .code-editor {
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 12px;
    line-height: 1.5;
    background-color: #1e1e1e;
    color: #d4d4d4;

    &::placeholder {
      color: #6a6a6a;
    }
  }

  .code-help {
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
