<script lang="ts" setup name="WorkflowListPage">
/**
 * 工作流列表页面
 * 卡片式展示工作流列表，支持新增、编辑、删除、发布、复制等操作
 */
import type {
  WorkflowPageResp,
  WorkflowTemplateResp,
} from '#/api/ai-workflow/types';

import { nextTick, onErrorCaptured, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { ApartmentOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { useFs } from '@fast-crud/fast-crud';
import { Input, message, Modal } from 'ant-design-vue';

import {
  copyWorkflow,
  createWorkflowFromTemplate,
  deleteWorkflow,
  publishWorkflow,
} from '#/api/ai-workflow';

import TemplateSelectModal from '../templates/components/TemplateSelectModal.vue';
import SaveAsTemplateModal from '../templates/components/SaveAsTemplateModal.vue';
import ApiKeyManager from '../components/ApiKeyManager.vue';
import WorkflowCard from './components/WorkflowCard.vue';
import createCrudOptions from './crud';

const router = useRouter();

const { crudBinding, crudRef, crudExpose } = useFs({
  createCrudOptions,
  context: {
    permission: 'ai:workflow',
  },
});

// 复制对话框
const copyModalVisible = ref(false);
const copyWorkflowName = ref('');
const copyingWorkflow = ref<WorkflowPageResp | null>(null);

// 模板选择弹窗
const templateSelectVisible = ref(false);

// API 访问抽屉（需求 4.2：从编辑页迁移到列表页）
const apiDrawerVisible = ref(false);
const apiDrawerItem = ref<WorkflowPageResp | null>(null);

// 保存为模块弹窗（需求 4.2）
const templateModalVisible = ref(false);
const templateModalItem = ref<WorkflowPageResp | null>(null);

// 捕获并忽略卡片模式下的 scrollTo 错误
onErrorCaptured((err) => {
  if (err?.message?.includes('querySelector')) {
    return false;
  }
  return true;
});

// 禁用表格滚动方法
const disableTableScroll = () => {
  const tableRef = crudExpose.getTableRef?.();
  if (tableRef) {
    tableRef.scrollTo = () => {};
  }
};

// 监听数据变化，持续禁用滚动
watch(
  () => crudBinding.value.data,
  () => {
    nextTick(disableTableScroll);
  },
);

onMounted(async () => {
  disableTableScroll();
  await crudExpose.doRefresh();
});

/** 新建工作流 */
function handleCreate() {
  templateSelectVisible.value = true;
}

/** 处理模板选择 */
async function handleTemplateSelect(template: WorkflowTemplateResp | null) {
  if (template) {
    // 使用模板创建工作流
    try {
      const workflowId = await createWorkflowFromTemplate(
        template.id,
        `${template.name} - 副本`,
        `基于模板「${template.name}」创建`,
      );
      message.success('创建成功');
      router.push(`/agent/workflow/editor/${workflowId}`);
    } catch {
      message.error('创建失败');
    }
  } else {
    // 创建空白工作流
    router.push('/agent/workflow/editor');
  }
}

/** 编辑工作流 */
function handleEdit(item: WorkflowPageResp) {
  router.push(`/agent/workflow/editor/${item.id}`);
}

/** 删除工作流 */
function handleRemove(item: WorkflowPageResp) {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除工作流「${item.name}」吗？删除后无法恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      await deleteWorkflow(item.id);
      message.success('删除成功');
      await crudExpose.doRefresh();
    },
  });
}

/** 发布工作流 */
async function handlePublish(item: WorkflowPageResp) {
  Modal.confirm({
    title: '确认发布',
    content: `确定要发布工作流「${item.name}」吗？发布后可以被执行。`,
    okText: '发布',
    cancelText: '取消',
    onOk: async () => {
      await publishWorkflow(item.id);
      message.success('发布成功');
      await crudExpose.doRefresh();
    },
  });
}

/** 打开复制对话框 */
function handleCopy(item: WorkflowPageResp) {
  copyingWorkflow.value = item;
  copyWorkflowName.value = `${item.name} - 副本`;
  copyModalVisible.value = true;
}

/** 确认复制 */
async function confirmCopy() {
  if (!copyingWorkflow.value || !copyWorkflowName.value.trim()) {
    message.warning('请输入工作流名称');
    return;
  }

  await copyWorkflow(copyingWorkflow.value.id, copyWorkflowName.value.trim());
  message.success('复制成功');
  copyModalVisible.value = false;
  copyingWorkflow.value = null;
  copyWorkflowName.value = '';
  await crudExpose.doRefresh();
}

/** 查看执行历史 */
function handleHistory(item: WorkflowPageResp) {
  router.push(`/agent/workflow/history/${item.id}`);
}

/** 执行工作流（未发布时提示先发布，需求 2.1） */
function handleExecute(item: WorkflowPageResp) {
  if (item.status !== 'PUBLISHED') {
    message.warning(`工作流「${item.name}」尚未发布，请先发布后再执行`);
    return;
  }
  router.push(`/agent/workflow/editor/${item.id}?execute=true`);
}

/** 打开 API 访问抽屉（需求 4.2） */
function handleApi(item: WorkflowPageResp) {
  apiDrawerItem.value = item;
  apiDrawerVisible.value = true;
}

/** 打开保存为模块弹窗（需求 4.2） */
function handleSaveAsTemplate(item: WorkflowPageResp) {
  templateModalItem.value = item;
  templateModalVisible.value = true;
}

/** 模块保存成功 */
function handleTemplateSaved() {
  message.success('保存为模块成功');
}
</script>

<template>
  <fs-page class="page-layout-card workflow-list-page">
    <fs-crud ref="crudRef" v-bind="crudBinding">
      <!-- 自定义新增按钮 -->
      <template #actionbar-left>
        <a-button type="primary" @click="handleCreate">
          <template #icon><PlusOutlined /></template>
          新建工作流
        </a-button>
      </template>

      <!-- 卡片列表 -->
      <div class="card-list-wrapper">
        <!-- 有数据时显示卡片网格 -->
        <div v-if="crudBinding.data?.length" class="card-grid">
          <WorkflowCard
            v-for="item of crudBinding.data"
            :key="item.id"
            :item="item"
            @edit="handleEdit"
            @remove="handleRemove"
            @publish="handlePublish"
            @copy="handleCopy"
            @history="handleHistory"
            @execute="handleExecute"
            @api="handleApi"
            @template="handleSaveAsTemplate"
          />
        </div>

        <!-- 空状态 -->
        <div v-else class="empty-state">
          <div class="empty-icon">
            <ApartmentOutlined />
          </div>
          <h3 class="empty-title">还没有工作流</h3>
          <p class="empty-desc">创建你的第一个 AI 工作流，开启自动化之旅</p>
          <a-button type="primary" size="large" @click="handleCreate">
            <template #icon><PlusOutlined /></template>
            新建工作流
          </a-button>
        </div>
      </div>
    </fs-crud>

    <!-- 复制对话框 -->
    <a-modal
      v-model:open="copyModalVisible"
      title="复制工作流"
      @ok="confirmCopy"
    >
      <a-form layout="vertical">
        <a-form-item label="工作流名称" required>
          <Input
            v-model:value="copyWorkflowName"
            placeholder="请输入新工作流名称"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 模板选择弹窗 -->
    <TemplateSelectModal
      v-model:open="templateSelectVisible"
      @select="handleTemplateSelect"
    />

    <!-- API 访问抽屉（需求 4.2） -->
    <a-drawer
      v-model:open="apiDrawerVisible"
      :title="`${apiDrawerItem?.name || ''} - API 访问管理`"
      placement="right"
      :width="720"
    >
      <ApiKeyManager
        v-if="apiDrawerItem"
        :workflow-id="apiDrawerItem.id"
      />
    </a-drawer>

    <!-- 保存为模块弹窗（需求 4.2） -->
    <SaveAsTemplateModal
      v-model:open="templateModalVisible"
      :workflow-id="templateModalItem?.id || ''"
      :workflow-name="templateModalItem?.name"
      @success="handleTemplateSaved"
    />
  </fs-page>
</template>

<style lang="less" scoped>
@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgb(24 144 255 / 20%);
    transform: scale(1);
  }

  50% {
    box-shadow: 0 0 0 15px rgb(24 144 255 / 0%);
    transform: scale(1.02);
  }
}

.workflow-list-page {
  :deep(.fs-crud-container) {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  :deep(.fs-search) {
    order: 1;
    margin-bottom: 16px;
  }

  :deep(.fs-actionbar) {
    order: 2;
    margin-bottom: 8px;
  }

  :deep(.fs-table-container) {
    display: none;
  }

  .card-list-wrapper {
    flex: 1;
    order: 3;
    padding: 8px 16px 16px;
    overflow: auto;
  }

  :deep(.fs-pagination) {
    order: 4;
    padding: 12px 16px;
    background: #fff;
    border-top: 1px solid #f0f0f0;
  }
}

// 卡片网格布局
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 24px;
}

// 空状态样式
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  padding: 60px 20px;
  text-align: center;

  .empty-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 120px;
    height: 120px;
    margin-bottom: 24px;
    font-size: 56px;
    color: #1890ff;
    background: linear-gradient(
      135deg,
      rgb(24 144 255 / 10%) 0%,
      rgb(114 46 209 / 10%) 100%
    );
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
  }

  .empty-title {
    margin: 0 0 8px;
    font-size: 20px;
    font-weight: 600;
    color: #333;
  }

  .empty-desc {
    margin: 0 0 24px;
    font-size: 14px;
    color: #999;
  }
}
</style>
