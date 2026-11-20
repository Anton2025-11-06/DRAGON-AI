<script lang="ts" setup>
/**
 * 系统提示词编辑器：固定高度 + 右下角上下扩缩手柄 + 大屏编辑弹框。
 *
 * 为什么要它：a-textarea 的 autosize 会让抽屉里的提示词框跟着内容无限变长，
 * 长提示词既看不全也点不到下面的字段。这里改成固定高度（内部滚动），右下角给
 * 一个 ns-resize 手柄按住上下拖即可扩缩，旁边再放「大屏编辑」弹框（沿用平台
 * 代码编辑那套 code-fullscreen-modal 口径）供长文本编辑与查看。
 * readonly 态仍允许打开大屏弹框，只是同样不可编辑。
 */
import { computed, ref } from 'vue';

import { ColumnHeightOutlined, FullscreenOutlined } from '@ant-design/icons-vue';

const props = withDefaults(
  defineProps<{
    /** 查看态：文本框与大屏弹框都只读 */
    disabled?: boolean;
    /** 初始高度（px），拖动手柄可在此上下限之间扩缩 */
    height?: number;
    /** 大屏弹框标题 */
    modalTitle?: string;
    modelValue: string;
    placeholder?: string;
  }>(),
  {
    disabled: false,
    height: 220,
    modalTitle: '系统提示词',
    placeholder: '留空则沿用平台默认基础段',
  },
);

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>();

const MIN_HEIGHT = 120;
const MAX_HEIGHT = 600;

const boxHeight = ref(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, props.height)));
const modalOpen = ref(false);

const value = computed(() => props.modelValue ?? '');

function onInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLTextAreaElement).value);
}

const boxStyle = computed(() => ({
  height: `${boxHeight.value}px`,
  resize: 'none' as const,
  overflowY: 'auto' as const,
}));

let dragging = false;
let startY = 0;
let startHeight = 0;

function stopResize() {
  dragging = false;
  window.removeEventListener('mousemove', onResize);
  window.removeEventListener('mouseup', stopResize);
}

function onResize(e: MouseEvent) {
  if (!dragging) return;
  const next = startHeight + (e.clientY - startY);
  boxHeight.value = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, next));
}

function startResize(e: MouseEvent) {
  if (props.disabled) return;
  dragging = true;
  startY = e.clientY;
  startHeight = boxHeight.value;
  window.addEventListener('mousemove', onResize);
  window.addEventListener('mouseup', stopResize);
}
</script>

<template>
  <div class="prompt-editor">
    <textarea
      class="prompt-box ant-input"
      :disabled="props.disabled"
      :placeholder="props.placeholder"
      :style="boxStyle"
      :value="value"
      @input="onInput"
    ></textarea>

    <div class="prompt-tools">
      <a-tooltip v-if="!props.disabled" title="按住上下拖动可调整高度">
        <a-button class="prompt-resize-btn" size="small" type="text" @mousedown="startResize">
          <template #icon>
            <ColumnHeightOutlined />
          </template>
        </a-button>
      </a-tooltip>
      <a-tooltip title="大屏编辑/查看">
        <a-button class="prompt-expand-btn" size="small" type="text" @click="modalOpen = true">
          <template #icon>
            <FullscreenOutlined />
          </template>
        </a-button>
      </a-tooltip>
    </div>

    <a-modal
      :footer="null"
      :fullscreen="true"
      :open="modalOpen"
      class="code-fullscreen-modal"
      :title="props.modalTitle"
      @update:open="modalOpen = $event"
    >
      <textarea
        class="prompt-box prompt-box-full ant-input"
        :disabled="props.disabled"
        :placeholder="props.placeholder"
        :value="value"
        @input="onInput"
      ></textarea>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.prompt-editor {
  position: relative;
}

.prompt-box {
  width: 100%;
  padding: 4px 11px 26px;
  font-family: inherit;
  line-height: 1.6;
}

.prompt-box-full {
  height: calc(100vh - 130px);
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.8;
}

.prompt-tools {
  position: absolute;
  right: 4px;
  bottom: 4px;
  display: flex;
  align-items: center;
  background: linear-gradient(to left, var(--ant-color-bg-container, #fff) 60%, transparent);

  .prompt-resize-btn {
    color: #bfbfbf;
    cursor: ns-resize;

    &:hover:not(:disabled) {
      color: #1890ff;
      background-color: rgba(24, 144, 255, 0.1);
    }
  }

  .prompt-expand-btn {
    color: #bfbfbf;

    &:hover:not(:disabled) {
      color: #1890ff;
      background-color: rgba(24, 144, 255, 0.1);
    }
  }
}
</style>
