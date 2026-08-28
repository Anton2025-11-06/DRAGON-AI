<template>
  <div class="variable-input" :class="{ focused: isFocused, disabled }">
    <!-- 输入区域 -->
    <div class="input-wrapper">
      <div
        ref="editorRef"
        class="editor"
        :contenteditable="!disabled"
        :placeholder="placeholder"
        @input="handleInput"
        @focus="handleFocus"
        @blur="handleBlur"
        @keydown="handleKeydown"
        @paste="handlePaste"
      />
    </div>

    <!-- 变量选择器按钮 -->
    <div class="input-actions">
      <VariableSelector
        :current-node-id="currentNodeId"
        :filter-types="filterTypes"
        button-text=""
        @select="handleVariableSelect"
      >
        <a-tooltip title="插入变量">
          <a-button
            type="text"
            size="small"
            :disabled="disabled"
            class="var-btn"
          >
            <template #icon>
              <CodeOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </VariableSelector>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * VariableInput 变量输入组件
 * 支持文本和变量混合输入，变量高亮显示
 * Requirements: 7.4
 */
import type { ExtendedVariableType } from '#/api/ai-workflow/types';
import type { NodeVariable, NodeWithVariables } from './VariableSelector.vue';

import { onMounted, ref, watch } from 'vue';
import { CodeOutlined } from '@ant-design/icons-vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';

import VariableSelector from './VariableSelector.vue';
import { formatWorkflowVariableReferenceLabel } from './variable-reference';

// ==================== Props & Emits ====================

interface Props {
  /** 输入值 */
  modelValue?: string;
  /** 占位符 */
  placeholder?: string;
  /** 当前节点ID */
  currentNodeId?: string;
  /** 是否禁用 */
  disabled?: boolean;
  /** 过滤变量类型 */
  filterTypes?: ExtendedVariableType[];
  /** 是否多行 */
  multiline?: boolean;
  /** 最大行数（多行模式） */
  maxRows?: number;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  placeholder: '输入内容，使用 {{变量}} 引用变量',
  currentNodeId: '',
  disabled: false,
  filterTypes: () => [],
  multiline: false,
  maxRows: 5,
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'change', value: string): void;
  (e: 'focus'): void;
  (e: 'blur'): void;
}>();

// ==================== 状态 ====================

/** 编辑器引用 */
const editorRef = ref<HTMLDivElement | null>(null);
const workflowStore = useAiWorkflowStore();

/** 是否聚焦 */
const isFocused = ref(false);

/** 变量正则表达式 */
const VARIABLE_REGEX = /\{\{([^}]+)\}\}/g;

// ==================== 方法 ====================

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function resolveNodeLabel(nodeId: string): string | undefined {
  const node = workflowStore.canvasRef
    ?.getNodes?.()
    .find((item: any) => item.id === nodeId);
  return node?.data?.label || undefined;
}

/**
 * 将文本转换为带高亮的 HTML
 * 变量引用格式：{{节点标签.变量名}}
 */
function textToHtml(text: string): string {
  if (!text) return '';

  // 转义 HTML 特殊字符
  const escaped = escapeHtml(text);

  // 高亮变量引用
  return escaped.replace(VARIABLE_REGEX, (_match, varPath) => {
    const label = formatWorkflowVariableReferenceLabel(
      varPath,
      resolveNodeLabel,
    );
    return `<span class="variable-tag" contenteditable="false" data-variable="${escapeHtml(varPath.trim())}">${escapeHtml(label)}</span>`;
  });
}

/**
 * 将 HTML 转换为纯文本
 */
function htmlToText(html: string): string {
  // 创建临时元素解析 HTML
  const temp = document.createElement('div');
  temp.innerHTML = html;

  // 遍历所有节点，提取文本
  let result = '';

  // 使用递归遍历，以便跳过 variable-tag 的子节点
  function traverse(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      result += node.textContent;
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const element = node as HTMLElement;
      if (element.classList.contains('variable-tag')) {
        // 对于变量标签，只提取 data-variable 属性，跳过子节点
        const varPath = element.getAttribute('data-variable');
        result += `{{${varPath}}}`;
        // 不遍历子节点，避免重复
        return;
      } else if (element.tagName === 'BR') {
        result += '\n';
      }
      // 遍历子节点
      for (const child of element.childNodes) {
        traverse(child);
      }
    }
  }

  // 遍历所有顶层子节点
  for (const child of temp.childNodes) {
    traverse(child);
  }

  return result;
}

/**
 * 设置编辑器内容
 */
function setEditorContent(text: string) {
  if (!editorRef.value) return;

  const html = textToHtml(text);
  editorRef.value.innerHTML = html || '';
}

/**
 * 获取编辑器内容
 */
function getEditorContent(): string {
  if (!editorRef.value) return '';
  return htmlToText(editorRef.value.innerHTML);
}

/**
 * 在光标位置插入文本
 */
function insertAtCursor(text: string) {
  if (!editorRef.value || props.disabled) return;

  editorRef.value.focus();

  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    // 如果没有选区，追加到末尾
    const currentText = getEditorContent();
    const newText = currentText + text;
    setEditorContent(newText);
    emit('update:modelValue', newText);
    emit('change', newText);
    return;
  }

  const range = selection.getRangeAt(0);
  range.deleteContents();

  // 创建变量标签
  const html = textToHtml(text);
  const temp = document.createElement('div');
  temp.innerHTML = html;

  // 插入节点
  const fragment = document.createDocumentFragment();
  while (temp.firstChild) {
    fragment.appendChild(temp.firstChild);
  }

  range.insertNode(fragment);

  // 移动光标到插入内容之后
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);

  // 更新值
  const newText = getEditorContent();
  emit('update:modelValue', newText);
  emit('change', newText);
}

/**
 * 处理输入
 */
function handleInput() {
  const text = getEditorContent();
  emit('update:modelValue', text);
  emit('change', text);
}

/**
 * 处理聚焦
 */
function handleFocus() {
  isFocused.value = true;
  emit('focus');
}

/**
 * 处理失焦
 */
function handleBlur() {
  isFocused.value = false;
  emit('blur');

  // 注意：移除了失焦时的重新渲染逻辑
  // 之前的逻辑会导致在某些情况下重复添加变量标签
  // 变量高亮已经在输入时处理，无需在失焦时再次处理
}

/**
 * 处理键盘事件
 */
function handleKeydown(event: KeyboardEvent) {
  // 单行模式下禁止换行
  if (!props.multiline && event.key === 'Enter') {
    event.preventDefault();
  }
}

/**
 * 处理粘贴
 */
function handlePaste(event: ClipboardEvent) {
  event.preventDefault();

  // 只粘贴纯文本
  const text = event.clipboardData?.getData('text/plain') || '';
  if (!text) return;

  // 单行模式下移除换行
  const processedText = props.multiline ? text : text.replace(/[\r\n]/g, ' ');

  insertAtCursor(processedText);
}

/**
 * 处理变量选择
 */
function handleVariableSelect(
  reference: string,
  _variable: NodeVariable,
  _node: NodeWithVariables,
) {
  insertAtCursor(reference);
}

// ==================== 监听 ====================

// 监听外部值变化
watch(
  () => props.modelValue,
  (newValue, oldValue) => {
    // 只有当新值与旧值不同时才更新，避免重复渲染
    if (newValue === oldValue) return;

    const currentValue = getEditorContent();
    // 规范化比较：去除首尾空格
    const normalizedNew = (newValue || '').trim();
    const normalizedCurrent = (currentValue || '').trim();

    if (normalizedNew !== normalizedCurrent) {
      setEditorContent(newValue);
    }
  },
);

// ==================== 生命周期 ====================

onMounted(() => {
  if (props.modelValue) {
    setEditorContent(props.modelValue);
  }
});

// 暴露方法
defineExpose({
  focus: () => editorRef.value?.focus(),
  blur: () => editorRef.value?.blur(),
  insertVariable: insertAtCursor,
  getValue: getEditorContent,
  setValue: setEditorContent,
});
</script>

<style scoped lang="less">
.variable-input {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background-color: #fff;
  transition: all 0.2s;

  &:hover:not(.disabled) {
    border-color: #40a9ff;
  }

  &.focused {
    border-color: #1890ff;
    box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
  }

  &.disabled {
    background-color: #f5f5f5;
    cursor: not-allowed;

    .editor {
      cursor: not-allowed;
      color: rgba(0, 0, 0, 0.25);
    }
  }

  .input-wrapper {
    flex: 1;
    min-height: 22px;
    overflow: hidden;
  }

  .editor {
    min-height: 22px;
    line-height: 22px;
    font-size: 14px;
    color: #262626;
    outline: none;
    word-break: break-all;

    &:empty::before {
      content: attr(placeholder);
      color: #bfbfbf;
      pointer-events: none;
    }

    // 变量标签样式
    :deep(.variable-tag) {
      display: inline-flex;
      align-items: center;
      padding: 0 6px;
      margin: 0 2px;
      font-size: 12px;
      font-family:
        'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      color: #722ed1;
      background-color: #f9f0ff;
      border: 1px solid #d3adf7;
      border-radius: 4px;
      cursor: default;
      user-select: none;
      vertical-align: middle;
      line-height: 18px;
    }
  }

  .input-actions {
    flex-shrink: 0;
    display: flex;
    align-items: center;

    .var-btn {
      color: #8c8c8c;
      padding: 0 4px;

      &:hover:not(:disabled) {
        color: #1890ff;
        background-color: #e6f7ff;
      }
    }
  }
}
</style>
