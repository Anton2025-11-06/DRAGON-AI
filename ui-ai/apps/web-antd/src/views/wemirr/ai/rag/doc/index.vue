<script lang="ts" setup name="RagDocumentManage">
/**
 * 知识维护页（菜单名已从「知识库文档」改掉，需求 3）。
 *
 * 编排：左「知识库列表」+ 右「文档面板（按类型三形态）」。
 * 这里只剩「维护」一件事：找库、传文档、看进度、管切片。
 * 原先挂在顶层的「多模态检索」「知识图谱」两个弹窗已提升为独立菜单页（需求 4）：
 *   知识检索 -> views/wemirr/ai/rag/retrieve/index.vue
 *   图谱检索 -> views/wemirr/ai/rag/graph/index.vue
 * 遗留的 fast-crud 文档表格 / 语义搜索抽屉 / 实体召回等旧组件已随契约重构移除。
 * 顶部不再放「刷新」整页按钮：左栏自带「查询」、右栏自带重加载图标，
 * 两个作用域不同的入口拧在一起只会让人猜这个按钮到底刷哪一半。
 */
import type { KnowledgeBaseResp } from './api';

import { ref } from 'vue';

import DocPanel from './DocPanel.vue';
import KnowledgeBaseList from './KnowledgeBaseList.vue';

const currentKb = ref<KnowledgeBaseResp | null>(null);

function onSelectKb(kb: KnowledgeBaseResp | null) {
  currentKb.value = kb;
}
</script>

<template>
  <div class="page-container">
    <div class="doc-row">
      <!-- 左侧知识库列表（定高 + 自带滚动条） -->
      <KnowledgeBaseList class="kb-list-col h-full" @select="onSelectKb" />

      <!-- 右侧文档面板（按 kbType 三形态，同样定高，分页后的文档在区内滚动） -->
      <div class="doc-panel-col">
        <DocPanel :kb="currentKb" />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.page-container {
  display: flex;
  flex-direction: column;
  /* 定高不能用 height:100%：布局链的根是 min-h-full（不是 h-full），<main> 的高度由内容
     决定，列表一长过视口整条百分比高度链就塌陷、改由整页滚动 —— 两栏的内部滚动条
     永远不出现，多出的条目直接被裁掉。这里用视口高减去头部容器与页脚（框架已以 CSS
     变量写在 :root 上），拿到不依赖自测量的确定高度。 */
  height: calc(
    100vh - var(--vben-header-height, 90px) - var(--vben-footer-height, 32px)
  );
  padding: 8px;
  overflow: hidden;
}

/* 两栏共吃剩下的整高。min-height: 0 是关键：flex 项默认 min-height: auto，
   不加这一条子栏会被内容顶高，滚动条永远弹不出来，多出的部分被父级 overflow 裁掉
   （表现就是「列表看不全、也滚不动」）。 */
.doc-row {
  display: flex;
  flex: 1;
  gap: 16px;
  min-height: 0;
}

.kb-list-col {
  flex: 0 0 320px;
  width: 320px;
  min-height: 0;
}

.doc-panel-col {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
