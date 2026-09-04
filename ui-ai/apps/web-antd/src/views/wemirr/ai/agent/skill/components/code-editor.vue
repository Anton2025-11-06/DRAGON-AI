<script lang="ts" setup>
/**
 * 轻量代码编辑器
 * 替代原 develop/gen 模块的 CodeEditor（该模块已随无关业务删除）
 * 基于 vue-codemirror，保持 v-model:command / read-only / height 用法不变
 */
import type { Extension } from '@codemirror/state';

import { computed } from 'vue';

import { javascript } from '@codemirror/lang-javascript';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { Codemirror } from 'vue-codemirror';

const model = defineModel<string>('command', { default: '' });

const props = withDefaults(
  defineProps<{
    /** 语言（文件扩展名，如 js/md/py），用于选择语法高亮 */
    language?: string;
    /** 是否只读 */
    readOnly?: boolean;
    /** 高度 */
    height?: string;
  }>(),
  {
    language: '',
    readOnly: false,
    height: '100%',
  },
);

/** 根据扩展名选择语法高亮 */
function getLanguageExtension(lang: string): Extension {
  const ext = (lang || '').toLowerCase();
  if (['md', 'markdown'].includes(ext)) {
    return markdown();
  }
  if (['py', 'python'].includes(ext)) {
    return python();
  }
  return javascript();
}

const extensions = computed<Extension[]>(() => [
  getLanguageExtension(props.language),
  oneDark,
]);
</script>

<template>
  <Codemirror
    v-model="model"
    :disabled="props.readOnly"
    :extensions="extensions"
    :style="{ height: props.height }"
    class="code-editor"
  />
</template>

<style scoped>
.code-editor {
  width: 100%;
  overflow: hidden;
  font-size: 13px;
  background: #1e1e1e;
  border-radius: 4px;

  :deep(.cm-editor) {
    height: 100%;
  }
}
</style>