<script lang="ts" setup name="WorkflowTemplatesPage">
/**
 * 工作流模板管理页面
 * 展示和管理工作流模板，支持内置模板和自定义模板
 */
import type { WorkflowTemplateResp } from '#/api/ai-workflow/types';

import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  AppstoreOutlined,
  ImportOutlined,
  PlusOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import { message, Modal, Spin, Tabs, Upload } from 'ant-design-vue';

import {
  createTemplate,
  createWorkflowFromTemplate,
  deleteTemplate,
  exportTemplate,
  getTemplatePage,
  importTemplate,
} from '#/api/ai-workflow';

import TemplateCard from './components/TemplateCard.vue';

const router = useRouter();

// 状态
const loading = ref(false);
const templates = ref<WorkflowTemplateResp[]>([]);
const activeCategory = ref('all');

// 分类配置
const categories = [
  { key: 'all', label: '全部模板' },
  { key: 'RAG', label: 'RAG问答' },
  { key: 'SUMMARY', label: '文档摘要' },
  { key: 'EXTRACTION', label: '数据提取' },
  { key: 'CONVERSATION', label: '多轮对话' },
  { key: 'GENERATION', label: '内容生成' },
];

// 加载模板列表
async function loadTemplates() {
  loading.value = true;
  try {
    const res = await getTemplatePage({ size: 100 });
    templates.value = res.records;
  } catch {
    message.error('加载模板失败');
  } finally {
    loading.value = false;
  }
}

// 获取当前分类的模板
function getTemplatesByCategory(category: string) {
  if (category === 'all') {
    return templates.value;
  }
  return templates.value.filter((t) => t.category === category);
}

// 使用模板创建工作流
async function handleUseTemplate(template: WorkflowTemplateResp) {
  Modal.confirm({
    title: '使用模板',
    content: `确定使用「${template.name}」模板创建新工作流吗？`,
    okText: '确定',
    cancelText: '取消',
    onOk: async () => {
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
    },
  });
}

// 删除模板
function handleRemoveTemplate(template: WorkflowTemplateResp) {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除模板「${template.name}」吗？删除后无法恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteTemplate(template.id);
        message.success('删除成功');
        loadTemplates();
      } catch {
        message.error('删除失败');
      }
    },
  });
}

// 导出模板
async function handleExportTemplate(template: WorkflowTemplateResp) {
  try {
    const json = await exportTemplate(template.id);
    // 创建下载链接
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${template.name}.json`;
    link.click();
    URL.revokeObjectURL(url);
    message.success('导出成功');
  } catch {
    message.error('导出失败');
  }
}

// 复制模板
async function handleCopyTemplate(template: WorkflowTemplateResp) {
  try {
    await createTemplate({
      name: `${template.name} - 副本`,
      description: template.description,
      category: template.category,
      icon: template.icon,
      graph: template.graph,
    });
    message.success('复制成功');
    loadTemplates();
  } catch {
    message.error('复制失败');
  }
}

// 导入模板
async function handleImport(file: File) {
  try {
    const text = await file.text();
    await importTemplate(text);
    message.success('导入成功');
    loadTemplates();
  } catch {
    message.error('导入失败，请检查文件格式');
  }
  return false; // 阻止默认上传行为
}

// 创建新模板
function handleCreateTemplate() {
  router.push('/agent/workflow/editor?saveAsTemplate=true');
}

onMounted(() => {
  loadTemplates();
});
</script>

<template>
  <div class="workflow-templates-page">
    <Spin :spinning="loading">
      <!-- 页面头部 -->
      <div class="page-header">
        <div class="header-left">
          <h2 class="page-title">
            <AppstoreOutlined />
            工作流模板
          </h2>
          <p class="page-desc">
            使用预定义模板快速创建工作流，或管理自定义模板
          </p>
        </div>
        <div class="header-right">
          <a-space>
            <Upload
              :before-upload="handleImport"
              :show-upload-list="false"
              accept=".json"
            >
              <a-button>
                <template #icon><ImportOutlined /></template>
                导入模板
              </a-button>
            </Upload>
            <a-button type="primary" @click="handleCreateTemplate">
              <template #icon><PlusOutlined /></template>
              新建模板
            </a-button>
          </a-space>
        </div>
      </div>

      <!-- 分类标签 -->
      <Tabs v-model:activeKey="activeCategory" class="category-tabs">
        <a-tab-pane v-for="cat in categories" :key="cat.key" :tab="cat.label" />
      </Tabs>

      <!-- 模板网格 -->
      <div class="template-grid">
        <TemplateCard
          v-for="template in getTemplatesByCategory(activeCategory)"
          :key="template.id"
          :item="template"
          @use="handleUseTemplate"
          @remove="handleRemoveTemplate"
          @export="handleExportTemplate"
          @copy="handleCopyTemplate"
        />
      </div>

      <!-- 空状态 -->
      <div
        v-if="!loading && getTemplatesByCategory(activeCategory).length === 0"
        class="empty-state"
      >
        <div class="empty-icon">
          <AppstoreOutlined />
        </div>
        <h3>暂无模板</h3>
        <p>当前分类下没有模板，您可以创建新模板或导入模板</p>
        <a-space>
          <Upload
            :before-upload="handleImport"
            :show-upload-list="false"
            accept=".json"
          >
            <a-button>
              <template #icon><UploadOutlined /></template>
              导入模板
            </a-button>
          </Upload>
          <a-button type="primary" @click="handleCreateTemplate">
            <template #icon><PlusOutlined /></template>
            新建模板
          </a-button>
        </a-space>
      </div>
    </Spin>
  </div>
</template>

<style lang="less" scoped>
.workflow-templates-page {
  padding: 24px;
  background: #f5f5f5;
  min-height: 100vh;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
  padding: 24px;
  background: #fff;
  border-radius: 8px;

  .header-left {
    .page-title {
      display: flex;
      gap: 8px;
      align-items: center;
      margin: 0 0 8px;
      font-size: 20px;
      font-weight: 600;
      color: #333;
    }

    .page-desc {
      margin: 0;
      font-size: 14px;
      color: #666;
    }
  }
}

.category-tabs {
  margin-bottom: 24px;
  padding: 0 16px;
  background: #fff;
  border-radius: 8px;
}

.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 24px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
  background: #fff;
  border-radius: 8px;

  .empty-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 80px;
    height: 80px;
    margin-bottom: 16px;
    font-size: 40px;
    color: #1890ff;
    background: #e6f7ff;
    border-radius: 50%;
  }

  h3 {
    margin: 0 0 8px;
    font-size: 16px;
    font-weight: 500;
    color: #333;
  }

  p {
    margin: 0 0 24px;
    font-size: 14px;
    color: #999;
  }
}
</style>
