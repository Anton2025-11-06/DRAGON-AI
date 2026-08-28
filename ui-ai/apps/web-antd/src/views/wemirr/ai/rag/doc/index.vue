<script lang="ts" setup name="RagDocumentManage">
/**
 * RAG 文档管理页面
 * 左侧：知识库列表（支持新增/编辑/删除）
 * 右侧：文档列表
 */
import { defineAsyncComponent, ref } from 'vue';

import { BookOutlined } from '@ant-design/icons-vue';
import { useFs } from '@fast-crud/fast-crud';
import { Card } from 'ant-design-vue';

import * as documentApi from './api';
import createCrudOptions from './crud';
import KnowledgeBaseList from './KnowledgeBaseList.vue';
import { useVectorizePolling } from './useVectorizePolling';

// 异步加载弹窗组件
const PreviewModal = defineAsyncComponent(() => import('./PreviewModal.vue'));
const PreviewChunkModal = defineAsyncComponent(
  () => import('./PreviewChunkModal.vue'),
);
const GraphVisualizationModal = defineAsyncComponent(
  () => import('./GraphVisualizationModal.vue'),
);
const EntityRecallTestModal = defineAsyncComponent(
  () => import('./EntityRecallTestModal.vue'),
);
const SemanticSearchDrawer = defineAsyncComponent(
  () => import('./SemanticSearchDrawer.vue'),
);

// 状态
const selectedKnowledgeBase = ref<any>(null);
const previewModalVisible = ref(false);
const previewChunkModalVisible = ref(false);
const previewDocumentId = ref<null | number>(null);
const previewDocumentTitle = ref<string>('');

// 图谱可视化
const graphModalVisible = ref(false);
const graphKnowledgeBase = ref<any>(null);

// 实体召回测试
const recallTestModalVisible = ref(false);
const recallTestKnowledgeBase = ref<any>(null);

// 语义搜索抽屉
const semanticSearchRef = ref<any>(null);

let refreshCrud: () => void;

// 向量化轮询
const { startVectorizeStatusPolling, stopAllPolling } = useVectorizePolling(
  () => refreshCrud?.(),
);

// 初始化 fast-crud
const crudRef = ref();
const { crudBinding, crudExpose } = useFs({
  crudRef,
  createCrudOptions,
  context: {
    selectedKnowledgeBase,
    vectorizeDocument: async (documentId: number) => {
      await documentApi.VectorizeDocument(documentId);
    },
    graphizeDocument: async (documentId: number) => {
      return await documentApi.GraphizeKnowledgeItem(documentId);
    },
    get startVectorizeStatusPolling() {
      return startVectorizeStatusPolling;
    },
    openPreviewModal: (documentId: number, documentTitle?: string) => {
      previewDocumentId.value = documentId;
      previewDocumentTitle.value = documentTitle || '';
      previewModalVisible.value = true;
    },
    openPreviewChunkModal: (documentId: number, documentTitle?: string) => {
      previewDocumentId.value = documentId;
      previewDocumentTitle.value = documentTitle || '';
      previewChunkModalVisible.value = true;
    },
  },
});

refreshCrud = crudExpose.doRefresh;

// 选择知识库
const handleSelectKnowledgeBase = (kb: any) => {
  selectedKnowledgeBase.value = kb;
  stopAllPolling();
  crudExpose.doRefresh();
};

// 打开图谱可视化
const handleOpenGraph = (kb: any) => {
  graphKnowledgeBase.value = kb;
  graphModalVisible.value = true;
};

// 打开召回测试（实体召回）
const handleOpenRecallTest = (kb: any) => {
  recallTestKnowledgeBase.value = kb;
  recallTestModalVisible.value = true;
};

// 打开语义搜索
const handleOpenSemanticSearch = (kb: any) => {
  semanticSearchRef.value?.drawerApi.setData({ knowledgeBase: kb });
  semanticSearchRef.value?.drawerApi.open();
};
</script>

<template>
  <div class="page-container">
    <div class="flex h-full gap-4">
      <!-- 左侧知识库列表 -->
      <KnowledgeBaseList
        class="h-full w-1/4"
        @select="handleSelectKnowledgeBase"
        @open-graph="handleOpenGraph"
        @open-recall-test="handleOpenRecallTest"
        @open-semantic-search="handleOpenSemanticSearch"
      />

      <!-- 右侧文档管理 -->
      <Card class="doc-card flex flex-1 flex-col" :bordered="false">
        <template #title>
          <div v-if="selectedKnowledgeBase" class="flex items-center">
            <BookOutlined class="mr-2 text-blue-500" />
            {{ selectedKnowledgeBase.name }} - 文档管理
          </div>
          <div v-else>请选择知识库</div>
        </template>
        <div class="h-full">
          <fs-crud ref="crudRef" v-bind="crudBinding" />
        </div>
      </Card>
    </div>

    <!-- 文档预览弹窗 -->
    <PreviewModal
      :open="previewModalVisible"
      :document-id="previewDocumentId"
      :document-title="previewDocumentTitle"
      @update:open="previewModalVisible = $event"
    />

    <!-- 文档分块预览弹窗 -->
    <PreviewChunkModal
      :open="previewChunkModalVisible"
      :document-id="previewDocumentId"
      :document-title="previewDocumentTitle"
      @update:open="previewChunkModalVisible = $event"
    />

    <!-- 图谱可视化弹窗 -->
    <GraphVisualizationModal
      :open="graphModalVisible"
      :knowledge-base-id="graphKnowledgeBase?.id ?? null"
      :knowledge-base-name="graphKnowledgeBase?.name"
      @update:open="graphModalVisible = $event"
    />

    <!-- 实体召回测试弹窗 -->
    <EntityRecallTestModal
      :open="recallTestModalVisible"
      :knowledge-base-id="recallTestKnowledgeBase?.id ?? null"
      :knowledge-base-name="recallTestKnowledgeBase?.name"
      @update:open="recallTestModalVisible = $event"
    />

    <!-- 语义搜索抽屉 -->
    <SemanticSearchDrawer ref="semanticSearchRef" />
  </div>
</template>

<style lang="less" scoped>
.page-container {
  height: 100%;
  padding: 8px;
  overflow: hidden;
}

.doc-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;

  :deep(.ant-card-body) {
    display: flex;
    flex: 1;
    flex-direction: column;
    padding: 6px;
    overflow: hidden;
  }
}
</style>
