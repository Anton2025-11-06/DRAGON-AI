<script setup lang="ts">
/**
 * REPLY 指定回复节点配置表单
 * 回复方式二选一：引用参数（回复参数的值）/ 自定义文本（支持 {{变量}} 模板）
 */
import { reactive, watch } from 'vue';

import { VariableInput } from '../variable-selector';

/**
 * 指定回复节点配置
 */
interface ReplyNodeConfig {
  replyType: 'TEXT' | 'VARIABLE';
  variableRef?: string;
  text?: string;
  outputVariable?: string;
}

// Props
interface Props {
  config: ReplyNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ReplyNodeConfig): void;
}>();

// 表单数据
const formData = reactive<ReplyNodeConfig>({
  replyType: 'TEXT',
  variableRef: '',
  text: '',
  outputVariable: 'output',
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    formData.replyType = config.replyType || 'TEXT';
    formData.variableRef = config.variableRef || '';
    formData.text = config.text || '';
    formData.outputVariable = config.outputVariable || 'output';
  },
  { immediate: true, deep: true },
);

/**
 * 处理配置变更
 */
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 回复方式：二选一 -->
    <a-form-item label="回复方式" required>
      <a-radio-group
        v-model:value="formData.replyType"
        class="reply-type-group"
        @change="handleChange"
      >
        <a-radio value="TEXT">自定义文本</a-radio>
        <a-radio value="VARIABLE">引用参数</a-radio>
      </a-radio-group>
    </a-form-item>

    <!-- 引用参数：回复参数的值 -->
    <a-form-item v-if="formData.replyType === 'VARIABLE'" label="引用参数" required>
      <VariableInput
        v-model="formData.variableRef"
        :current-node-id="nodeId"
        placeholder="选择要回复的参数"
        class="reply-field"
        @change="handleChange"
      />
      <div class="field-tip">执行时将回复所选参数的值（对象/数组自动序列化为 JSON）</div>
    </a-form-item>

    <!-- 自定义文本：回复文本内容 -->
    <a-form-item v-else label="回复内容" required>
      <VariableInput
        v-model="formData.text"
        :current-node-id="nodeId"
        :placeholder="'输入回复内容，使用 {{变量}} 引用'"
        :max-rows="6"
        class="reply-field"
        @change="handleChange"
      />
      <!-- v-pre:{{节点.变量}} 是模板说明文字,不能写成插值(会解析 undefined.变量 报错) -->
      <div v-pre class="field-tip">支持 {{节点.变量}} 模板引用，未解析的引用渲染为空</div>
    </a-form-item>

    <!-- 输出变量名 -->
    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="output"
        @change="handleChange"
      />
      <div class="field-tip">回复内容在节点输出中使用的变量名</div>
    </a-form-item>

    <!-- 使用说明 -->
    <a-alert type="info" show-icon class="reply-help">
      <template #message>指定回复节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>
            <strong>引用参数</strong>：回复所选上游节点参数的值，保持原始类型，对象/数组自动 JSON 序列化
          </li>
          <li>
            <strong>自定义文本</strong>：回复你输入的文本，可用 <code v-pre>{{ 节点.变量 }}</code> 模板引用
          </li>
          <li>两者只能选择一种，回复内容会随工作流结果一并返回</li>
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
}

.reply-type-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.reply-field {
  width: 100%;
}

.field-tip {
  margin-top: 6px;
  font-size: 12px;
  color: #8c8c8c;
  line-height: 18px;
}

.reply-help {
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

      code {
        padding: 2px 6px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 3px;
      }
    }
  }
}
</style>