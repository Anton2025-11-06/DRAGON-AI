<script setup lang="ts">
/**
 * Markdown 渲染组件
 * 支持 Markdown 渲染和代码语法高亮
 *
 */

import { computed, onMounted, ref, watch } from 'vue';

import hljs from 'highlight.js';
import { marked } from 'marked';

// 导入 highlight.js 样式
import 'highlight.js/styles/github.css';

// ==================== Props ====================

interface Props {
  /** Markdown 内容 */
  content: string;
  /** 是否启用代码高亮 */
  enableHighlight?: boolean;
  /** 是否启用 GFM (GitHub Flavored Markdown) */
  enableGfm?: boolean;
  /** 是否启用换行符转换 */
  enableBreaks?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  enableHighlight: true,
  enableGfm: true,
  enableBreaks: true,
});

// ==================== 状态 ====================

const containerRef = ref<HTMLElement | null>(null);

// ==================== 配置 marked ====================

/**
 * 配置 marked 选项
 */
function configureMarked() {
  marked.setOptions({
    gfm: props.enableGfm,
    breaks: props.enableBreaks,
    async: false,
  });
}

// ==================== 计算属性 ====================

/**
 * 渲染后的 HTML 内容
 */
const renderedContent = computed(() => {
  if (!props.content) {
    return '';
  }

  try {
    configureMarked();
    return sanitizeHtml(marked.parse(props.content) as string);
  } catch {
    return `<pre>${escapeHtml(props.content)}</pre>`;
  }
});

// ==================== 工具函数 ====================

/**
 * 转义 HTML 特殊字符
 */
function escapeHtml(text: string): string {
  const map: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  };
  return text.replaceAll(/[&<>"']/g, (m) => map[m] || m);
}

function sanitizeHtml(html: string): string {
  const template = document.createElement('template');
  template.innerHTML = html;
  const blockedTags = new Set([
    'EMBED',
    'IFRAME',
    'LINK',
    'META',
    'OBJECT',
    'SCRIPT',
    'STYLE',
  ]);

  const walk = (node: Node) => {
    if (node instanceof HTMLElement) {
      if (blockedTags.has(node.tagName)) {
        node.remove();
        return;
      }
      for (const attr of node.attributes) {
        const name = attr.name.toLowerCase();
        const value = attr.value.trim().toLowerCase();
        if (
          name.startsWith('on') ||
          value.startsWith('javascript:') ||
          value.startsWith('data:text/html')
        ) {
          node.removeAttribute(attr.name);
        }
      }
    }
    [...node.childNodes].forEach(walk);
  };

  [...template.content.childNodes].forEach(walk);
  return template.innerHTML;
}

/**
 * 高亮代码块
 */
function highlightCodeBlocks() {
  if (!props.enableHighlight || !containerRef.value) {
    return;
  }

  const codeBlocks = containerRef.value.querySelectorAll('pre code');
  codeBlocks.forEach((block) => {
    // 检查是否已经高亮过
    if (!block.classList.contains('hljs')) {
      hljs.highlightElement(block as HTMLElement);
    }
  });
}

// ==================== 生命周期 ====================

onMounted(() => {
  highlightCodeBlocks();
});

// 监听内容变化，重新高亮
watch(
  () => props.content,
  () => {
    // 使用 nextTick 确保 DOM 已更新
    setTimeout(() => {
      highlightCodeBlocks();
    }, 0);
  },
);
</script>

<template>
  <div
    ref="containerRef"
    class="markdown-renderer"
    v-html="renderedContent"
  ></div>
</template>

<style lang="less" scoped>
.markdown-renderer {
  font-size: 14px;
  line-height: 1.6;
  color: var(--ant-color-text);
  word-wrap: break-word;

  // 标题样式
  :deep(h1),
  :deep(h2),
  :deep(h3),
  :deep(h4),
  :deep(h5),
  :deep(h6) {
    margin-top: 24px;
    margin-bottom: 16px;
    font-weight: 600;
    line-height: 1.25;
  }

  :deep(h1) {
    padding-bottom: 0.3em;
    font-size: 2em;
    border-bottom: 1px solid var(--ant-color-border);
  }

  :deep(h2) {
    padding-bottom: 0.3em;
    font-size: 1.5em;
    border-bottom: 1px solid var(--ant-color-border);
  }

  :deep(h3) {
    font-size: 1.25em;
  }

  :deep(h4) {
    font-size: 1em;
  }

  :deep(h5) {
    font-size: 0.875em;
  }

  :deep(h6) {
    font-size: 0.85em;
    color: var(--ant-color-text-secondary);
  }

  // 段落样式
  :deep(p) {
    margin-top: 0;
    margin-bottom: 16px;
  }

  // 链接样式
  :deep(a) {
    color: var(--ant-color-primary);
    text-decoration: none;

    &:hover {
      text-decoration: underline;
    }
  }

  // 代码块样式
  :deep(pre) {
    padding: 16px;
    margin-bottom: 16px;
    overflow-x: auto;
    font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
    font-size: 13px;
    line-height: 1.45;
    background-color: var(--ant-color-bg-layout);
    border-radius: 6px;

    code {
      padding: 0;
      font-size: inherit;
      line-height: inherit;
      word-wrap: normal;
      white-space: pre;
      background-color: transparent;
      border: 0;
    }
  }

  // 行内代码样式
  :deep(code:not(pre code)) {
    padding: 0.2em 0.4em;
    margin: 0;
    font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;
    font-size: 85%;
    background-color: var(--ant-color-bg-layout);
    border-radius: 4px;
  }

  // 引用块样式
  :deep(blockquote) {
    padding: 0 1em;
    margin: 0 0 16px;
    color: var(--ant-color-text-secondary);
    border-left: 4px solid var(--ant-color-border);

    > :first-child {
      margin-top: 0;
    }

    > :last-child {
      margin-bottom: 0;
    }
  }

  // 列表样式
  :deep(ul),
  :deep(ol) {
    padding-left: 2em;
    margin-top: 0;
    margin-bottom: 16px;
  }

  :deep(li) {
    margin-bottom: 4px;

    > p {
      margin-top: 16px;
    }

    + li {
      margin-top: 4px;
    }
  }

  :deep(ul ul),
  :deep(ul ol),
  :deep(ol ol),
  :deep(ol ul) {
    margin-top: 0;
    margin-bottom: 0;
  }

  // 表格样式
  :deep(table) {
    display: block;
    width: 100%;
    max-width: 100%;
    margin-bottom: 16px;
    overflow: auto;
    border-spacing: 0;
    border-collapse: collapse;

    th,
    td {
      padding: 8px 13px;
      border: 1px solid var(--ant-color-border);
    }

    th {
      font-weight: 600;
      background-color: var(--ant-color-bg-layout);
    }

    tr {
      background-color: var(--ant-color-bg-container);
      border-top: 1px solid var(--ant-color-border);

      &:nth-child(2n) {
        background-color: var(--ant-color-bg-layout);
      }
    }
  }

  // 水平线样式
  :deep(hr) {
    height: 0.25em;
    padding: 0;
    margin: 24px 0;
    background-color: var(--ant-color-border);
    border: 0;
  }

  // 图片样式
  :deep(img) {
    max-width: 100%;
    box-sizing: content-box;
    background-color: var(--ant-color-bg-container);
    border-radius: 4px;
  }

  // 任务列表样式
  :deep(.task-list-item) {
    list-style-type: none;

    input[type='checkbox'] {
      margin: 0 0.2em 0.25em -1.6em;
      vertical-align: middle;
    }
  }

  // 删除线样式
  :deep(del) {
    color: var(--ant-color-text-secondary);
  }

  // 强调样式
  :deep(strong) {
    font-weight: 600;
  }

  :deep(em) {
    font-style: italic;
  }
}

// 暗色模式适配
html[class='dark'] {
  .markdown-renderer {
    :deep(pre) {
      background-color: #1e1e1e;
    }

    :deep(code:not(pre code)) {
      background-color: #333;
    }

    // 暗色模式下的代码高亮
    :deep(.hljs) {
      background-color: #1e1e1e;
      color: #d4d4d4;
    }
  }
}
</style>
