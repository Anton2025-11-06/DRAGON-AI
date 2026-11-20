<script setup lang="ts">
/**
 * 深度探索输入器：正文与「智能体/技能/MCP/工具/知识库」图标芯片穿插在同一行。
 *
 * 为什么不是 textarea：textarea 里塞不下元素，芯片只能堆在输入框上方一行，
 * 资源一多就把消息区顶掉。contenteditable 可以让图标像文字一样落在句子中间
 * （「💡技能 帮我查一下 XXX，再用 🔌MCP 看一下 YYY」）。
 *
 * 状态归属：资源清单（挂了哪些、选了哪个智能体）仍以父组件为准，这里只是它的
 * 视图 + 采集器——父组件确认面板后调 insertChips/syncChips 写进来，用户在框里
 * 退格或点叉删掉芯片时向上报 remove，父组件减对应挂载，两边不会各写各的。
 */
import type { Component } from 'vue';

import type { ComposerChip, ComposerChipSource } from './composer';

import { h, nextTick, onMounted, onUnmounted, ref, render } from 'vue';

import {
  ApiOutlined,
  BulbOutlined,
  CloseOutlined,
  DatabaseOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';

const props = defineProps<{ placeholder?: string }>();

const emit = defineEmits<{
  (e: 'keydown', event: KeyboardEvent): void;
  (e: 'remove', chip: ComposerChip): void;
  /** 框内正文：芯片按 @名称 参与序列化，发出去的文本与用户看到的一致 */
  (e: 'update:text', text: string): void;
}>();

/** 芯片在纯文本里的写法：让模型知道句中这个位置指的是哪个资源 */
const CHIP_MENTION = '@';

/** 芯片图标是命令式插进 DOM 的，走不了模板，只能用 vue 的 render 挂上去 */
const CHIP_ICONS: Record<ComposerChipSource, Component> = {
  agent: RobotOutlined,
  kb: DatabaseOutlined,
  mcp: ApiOutlined,
  skill: BulbOutlined,
  tool: ToolOutlined,
};

const editorRef = ref<HTMLDivElement | null>(null);
const isEmpty = ref(true);
/** 图标 vnode 的卸载器：芯片被删时要显式 render(null)，否则组件实例悬着不放 */
const disposers = new WeakMap<HTMLElement, () => void>();
/** 打开资源面板前光标在框内哪一处：面板里的搜索框会抢焦点，插芯片要回位 */
let savedRange: null | Range = null;
/** 上一次采集到的芯片，用于 input 后 diff 出「用户刚删掉了哪几个」 */
let lastChips: ComposerChip[] = [];

const chipId = (chip: ComposerChip) => `${chip.source}:${chip.key}`;

function readChip(el: HTMLElement): ComposerChip {
  return {
    key: el.dataset.key ?? '',
    name: el.dataset.name ?? '',
    source: (el.dataset.source ?? 'skill') as ComposerChipSource,
  };
}

function chipElements(): HTMLElement[] {
  const el = editorRef.value;
  return el
    ? [...el.querySelectorAll<HTMLElement>('[data-chip]')].filter(
        (node) => node.dataset.chip === '1',
      )
    : [];
}

function chips(): ComposerChip[] {
  return chipElements().map((node) => readChip(node));
}

/** 归一后的一行：文本片段与芯片按到达顺序混排 */
type SegmentView = { chip?: ComposerChip; text?: string };
type LineView = SegmentView[];

function currentLine(lines: LineView[]): LineView {
  const last = lines.at(-1);
  if (last) return last;
  const line: LineView = [];
  lines.push(line);
  return line;
}

/**
 * 另起一行。hard=true 是真正的换行（文本里的 \n、软回车 BR）；
 * 块级标签走非 hard：首个块可以直接用掉那一行，免得整段内容开头多一个空行。
 */
function breakLine(lines: LineView[], hard = false) {
  if (!hard && lines.length === 1 && currentLine(lines).length === 0) return;
  lines.push([]);
}

function pushText(lines: LineView[], text: string) {
  // 文本里自带 \n 是还原草稿后的硬换行；相邻文本不用合段，行内按顺序拼回去结果一样
  text.split('\n').forEach((part, index) => {
    if (index > 0) breakLine(lines, true);
    if (part) currentLine(lines).push({ text: part });
  });
}

function pushChip(lines: LineView[], chip: ComposerChip) {
  currentLine(lines).push({ chip });
}

/** 只由 BR 与空文本构成的块：浏览器给光标造的空行占位，块本身已经断过一行 */
function isBlankBlock(el: HTMLElement | null): boolean {
  if (!el || (el.tagName !== 'DIV' && el.tagName !== 'P')) return false;
  return [...el.childNodes].every((node) => {
    if (node.nodeType === Node.ELEMENT_NODE) {
      return (node as HTMLElement).tagName === 'BR';
    }
    return !(node.nodeValue ?? '');
  });
}

/**
 * DOM → 行模型。
 * contenteditable 里按一次回车，浏览器写成 <div><br></div>：块本身就代表换行，
 * 里面那个只用来放光标的 BR 不能再算一次，否则一个回车会多出好几个空行。
 */
function collectLines(node: Node, lines: LineView[]) {
  node.childNodes.forEach((child) => {
    if (child.nodeType === Node.TEXT_NODE) {
      pushText(lines, child.nodeValue ?? '');
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    const el = child as HTMLElement;
    if (el.dataset.chip === '1') {
      pushChip(lines, readChip(el));
      return;
    }
    if (el.tagName === 'BR') {
      // 空行占位块里的 BR 只是光标落脚点；真正的软回车才另起一行
      if (isBlankBlock(el.parentElement)) return;
      breakLine(lines, true);
      return;
    }
    if (el.tagName === 'DIV' || el.tagName === 'P') {
      breakLine(lines);
      collectLines(el, lines);
      return;
    }
    // span/strong 等内联标签（含芯片内部的图标）并入当前行
    collectLines(el, lines);
  });
}

function readLines(): LineView[] {
  const el = editorRef.value;
  if (!el) return [];
  const lines: LineView[] = [[]];
  collectLines(el, lines);
  return lines;
}

/** 行模型 → 正文：芯片按 @名称 参与序列化，发出去的文本与框内看到的一致 */
function linesToText(lines: LineView[]): string {
  return lines
    .map((line) =>
      line
        .map((seg) =>
          seg.chip ? `${CHIP_MENTION}${seg.chip.name}` : (seg.text ?? ''),
        )
        .join(''),
    )
    .join('\n')
    .replaceAll('\u00A0', ' ');
}

function getText(): string {
  return linesToText(readLines());
}

function syncText() {
  const text = getText();
  isEmpty.value = text.trim().length === 0 && chips().length === 0;
  emit('update:text', text);
}

/** DOM 被本组件改完后统一收口：刷新芯片快照 + 向上同步正文 */
function afterMutation() {
  lastChips = chips();
  syncText();
}

function createChip(chip: ComposerChip): HTMLElement {
  const el = document.createElement('span');
  el.className = `hermes-chip is-${chip.source}`;
  el.contentEditable = 'false';
  el.dataset.chip = '1';
  el.dataset.source = chip.source;
  el.dataset.key = chip.key;
  el.dataset.name = chip.name;
  el.title = chip.name;

  const icon = document.createElement('span');
  icon.className = 'hermes-chip-icon';
  const label = document.createElement('span');
  label.className = 'hermes-chip-name';
  label.textContent = chip.name;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'hermes-chip-close';
  close.dataset.chipClose = '1';
  close.setAttribute('aria-label', `移除${chip.name}`);
  close.tabIndex = -1;

  render(h(CHIP_ICONS[chip.source] ?? RobotOutlined), icon);
  render(h(CloseOutlined), close);
  disposers.set(el, () => {
    render(null, icon);
    render(null, close);
  });

  el.append(icon, label, close);
  return el;
}

function destroyChip(el: HTMLElement) {
  disposers.get(el)?.();
  disposers.delete(el);
  el.remove();
}

function caretRange(): null | Range {
  const el = editorRef.value;
  const sel = window.getSelection();
  if (!el || !sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  return el.contains(range.commonAncestorContainer) ? range : null;
}

/** 焦点被搜索框抢走后也能回到原位：savedRange 还有效就用它，否则落到内容末尾 */
function restoreCaret() {
  const el = editorRef.value;
  const sel = window.getSelection();
  if (!el || !sel) return;
  if (savedRange && el.contains(savedRange.startContainer)) {
    sel.removeAllRanges();
    sel.addRange(savedRange);
    return;
  }
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

function onSelectionChange() {
  const range = caretRange();
  // 选区跑出输入框（比如点了搜索框）不清，插芯片还要用最后这一次有效位置
  if (range) savedRange = range.cloneRange();
}

function onMountedInit() {
  document.addEventListener('selectionchange', onSelectionChange);
}

function onBeforeUnmountInit() {
  document.removeEventListener('selectionchange', onSelectionChange);
}

onMounted(onMountedInit);
onUnmounted(onBeforeUnmountInit);

/** 光标前是否已有非空白内容：芯片前后各留一个空格，否则和文字粘在一起读不顺 */
function endsWithSpace(range: null | Range): boolean {
  const el = editorRef.value;
  if (!el) return true;
  if (!range) return true;
  const probe = document.createRange();
  probe.selectNodeContents(el);
  probe.setEnd(range.startContainer, range.startOffset);
  const before = probe.toString();
  return before.length === 0 || /\s$/.test(before);
}

// ==================== 对外方法 ====================

function focus() {
  const el = editorRef.value;
  if (!el) return;
  el.focus({ preventScroll: true });
  restoreCaret();
}

function saveCaret() {
  const range = caretRange();
  if (range) savedRange = range.cloneRange();
}

function clear() {
  const el = editorRef.value;
  if (!el) return;
  chipElements().forEach((node) => destroyChip(node));
  el.replaceChildren();
  savedRange = null;
  afterMutation();
}

/** 在光标处插入芯片（面板里新勾选的资源），并补空格、把光标放到芯片之后 */
function insertChips(list: ComposerChip[]) {
  const el = editorRef.value;
  if (!el || list.length === 0) return;
  el.focus({ preventScroll: true });
  restoreCaret();
  const range = caretRange();
  if (!range) {
    list.forEach((chip) => el.append(createChip(chip), ' '));
    afterMutation();
    return;
  }
  const frag = document.createDocumentFragment();
  if (!endsWithSpace(range)) frag.append(' ');
  list.forEach((chip) => frag.append(createChip(chip), ' '));
  const last = frag.lastChild;
  range.deleteContents();
  range.insertNode(frag);
  if (last) {
    const next = document.createRange();
    next.setStartAfter(last);
    next.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(next);
  }
  savedRange = null;
  afterMutation();
}

/**
 * 让框内芯片与给定的资源清单对齐：多余的删掉、缺的补在末尾。
 * 用于切会话恢复草稿、以及没有草稿时把已挂载资源铺成芯片。
 */
function syncChips(list: ComposerChip[]) {
  const el = editorRef.value;
  if (!el) return;
  const want = new Set(list.map((chip) => chipId(chip)));
  chipElements().forEach((node) => {
    if (!want.has(chipId(readChip(node)))) destroyChip(node);
  });
  const have = new Set(chips().map((chip) => chipId(chip)));
  const missing = list.filter((chip) => !have.has(chipId(chip)));
  if (missing.length > 0) {
    const frag = document.createDocumentFragment();
    missing.forEach((chip) => frag.append(createChip(chip), ' '));
    el.append(frag);
  }
  afterMutation();
}

function getHtml(): string {
  return editorRef.value?.innerHTML ?? '';
}

/** 行模型 → 编辑器 DOM：只放文本节点与芯片，换行交给 white-space: pre-wrap，不再产生块标签 */
function renderLines(lines: LineView[], target: HTMLElement) {
  const frag = document.createDocumentFragment();
  lines.forEach((line, index) => {
    if (index > 0) frag.append('\n');
    line.forEach((seg) => {
      if (seg.chip) frag.append(createChip(seg.chip), ' ');
      else if (seg.text) frag.append(seg.text);
    });
  });
  target.replaceChildren(frag);
}

/**
 * 还原会话草稿。草稿只在本页内存里往返，但仍然只认白名单：走一遍行模型，
 * 芯片按 data-* 重建、文本只当纯文本，其余标签全部拍平，杜绝脚本片段经 innerHTML 回流。
 */
function setHtml(html: string): ComposerChip[] {
  const el = editorRef.value;
  if (!el) return [];
  const holder = document.createElement('div');
  holder.innerHTML = html;
  chipElements().forEach((node) => destroyChip(node));
  const lines: LineView[] = [[]];
  collectLines(holder, lines);
  renderLines(lines, el);
  savedRange = null;
  afterMutation();
  return chips();
}

// ==================== 编辑器原生交互 ====================

function onInput() {
  // 一次 input 可能删掉多个芯片（框选后删除），逐个上报由父组件减挂载
  const now = chips();
  const nowIds = new Set(now.map((chip) => chipId(chip)));
  for (const prev of lastChips) {
    if (!nowIds.has(chipId(prev))) emit('remove', prev);
  }
  lastChips = now;
  syncText();
}

function onPaste(event: ClipboardEvent) {
  event.preventDefault();
  const text = event.clipboardData?.getData('text/plain') ?? '';
  if (!text) return;
  // execCommand 保住原生撤销栈；不支持时退化为直接插文本节点
  if (!document.execCommand('insertText', false, text)) {
    insertPlainText(text);
  }
  syncText();
}

function insertPlainText(text: string) {
  const el = editorRef.value;
  const range = caretRange();
  if (!el || !range) {
    el?.append(text);
    return;
  }
  const node = document.createTextNode(text);
  range.deleteContents();
  range.insertNode(node);
  const next = document.createRange();
  next.setStartAfter(node);
  next.collapse(true);
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(next);
}

function onClick(event: MouseEvent) {
  const target = event.target as HTMLElement;
  const closeBtn = target.closest?.('[data-chip-close]');
  if (!closeBtn) return;
  const chipEl = closeBtn.closest<HTMLElement>('[data-chip]');
  if (!chipEl) return;
  const chip = readChip(chipEl);
  destroyChip(chipEl);
  // 芯片被点掉的这次不要走 input 事件（点击删除不触发 input），这里直接报上去
  emit('remove', chip);
  afterMutation();
}

function onKeydown(event: KeyboardEvent) {
  // Enter 发送 / Shift+Enter 换行、/ 与 @ 唤面板都由父组件分流
  emit('keydown', event);
}

/** 框内已有芯片时，父组件恢复挂载后据此判断要不要再补插 */
function hasChip(chip: ComposerChip): boolean {
  return chips().some((item) => chipId(item) === chipId(chip));
}

function scrollToCaret() {
  nextTick(() => {
    const el = editorRef.value;
    if (el && el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
  });
}

defineExpose({
  clear,
  focus,
  getChips: chips,
  getHtml,
  getText,
  hasChip,
  insertChips,
  restoreCaret,
  saveCaret,
  scrollToCaret,
  setHtml,
  syncChips,
});
</script>

<template>
  <div
    ref="editorRef"
    class="hermes-composer-editor"
    :class="{ 'is-empty': isEmpty }"
    :data-placeholder="props.placeholder"
    contenteditable="true"
    role="textbox"
    aria-multiline="true"
    spellcheck="false"
    @input="onInput"
    @click="onClick"
    @keydown="onKeydown"
    @paste="onPaste"
  ></div>
</template>

<style scoped lang="less">
.hermes-composer-editor {
  min-height: 76px;
  max-height: 240px;
  padding: 4px 2px 8px;
  overflow-y: auto;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-color-1, #1f2329);
  white-space: pre-wrap;
  word-break: break-word;
  outline: none;
  cursor: text;

  &.is-empty::before {
    color: var(--text-color-3, #a0a6b0);
    content: attr(data-placeholder);
    pointer-events: none;
  }

  // 芯片是命令式建的，模板上的 scoped 标记轮不到它们，只能用 :deep 命中
  :deep(.hermes-chip) {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    max-width: 220px;
    margin: 0 1px;
    padding: 0 4px 0 7px;
    font-size: 12px;
    line-height: 22px;
    color: var(--text-color-2, #3c4254);
    vertical-align: baseline;
    background: var(--hover-bg-color, #f2f5fa);
    border: 1px solid var(--border-color, #e3e8ef);
    border-radius: 7px;
    user-select: none;

    .hermes-chip-icon {
      display: inline-flex;
      flex-shrink: 0;
      align-items: center;
      color: hsl(var(--primary));
      font-size: 12px;
    }

    .hermes-chip-name {
      overflow: hidden;
      font-weight: 500;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-chip-close {
      display: inline-flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      padding: 0;
      color: var(--text-color-3, #8a9099);
      cursor: pointer;
      background: transparent;
      border: 0;
      border-radius: 4px;

      .anticon {
        font-size: 9px;
      }

      &:hover {
        color: var(--text-color-1, #1f2329);
        background: rgb(15 23 42 / 8%);
      }
    }

    &.is-agent {
      color: #5b4db2;
      background: rgb(112 89 184 / 8%);
      border-color: rgb(112 89 184 / 20%);

      .hermes-chip-icon {
        color: #7059b8;
      }
    }

    &.is-mcp .hermes-chip-icon {
      color: #c2410c;
    }

    &.is-tool .hermes-chip-icon {
      color: #15803d;
    }

    &.is-kb .hermes-chip-icon {
      color: #0f766e;
    }

    &.is-skill .hermes-chip-icon {
      color: #b45309;
    }
  }
}
</style>
