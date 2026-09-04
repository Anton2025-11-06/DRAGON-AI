<script setup lang="ts">
/**
 * 模型广场 - 模型列表
 * maxkb 模型管理页面的 ui-ai（vben + antd）适配版：
 * - 提供商在添加/编辑弹窗内下拉单选（无左侧提供商导航栏）
 * - 添加模型弹窗保留 maxkb 数据结构设计（基础信息/凭据/高级设置）
 * - 查看人人可看；申请使用人人可点；增删改由 RBAC 权限（system:model:*）控制
 */
import { computed, onMounted, reactive, ref } from 'vue';

import { useAccess } from '@vben/access';

import { message, Modal } from 'ant-design-vue';
import { MdPreview } from 'md-editor-v3';

import { resolveApiUrl } from '#/api/helper';

import * as api from './api';
import CreateModelDialog from './components/CreateModelDialog.vue';
import ModelCard from './components/ModelCard.vue';

import 'md-editor-v3/lib/style.css';

// ==================== RBAC 权限 ====================
const { hasPermission } = useAccess();
const canAdd = computed(() => hasPermission('system:model:add'));
const canEdit = computed(() => hasPermission('system:model:edit'));
const canDelete = computed(() => hasPermission('system:model:delete'));

// ==================== 列表 ====================

/** 分类筛选字典 */
const categoryOptions = ref<{ label: string; value: string }[]>([]);

const loading = ref(false);
const modelList = ref<api.ModelPageRep[]>([]);
const total = ref(0);
const query = reactive<api.ModelPageReq>({
  current: 1,
  size: 10,
  name: '',
  category: undefined,
});

const load = async () => {
  loading.value = true;
  try {
    const res: any = await api.PageList({ ...query });
    modelList.value = res?.items || [];
    total.value = res?.total || 0;
  } finally {
    loading.value = false;
  }
};

const search = () => {
  query.current = 1;
  load();
};

// ==================== 添加/编辑弹窗 ====================
const dialogOpen = ref(false);
const dialogProvider = ref('');
const editingModel = ref<api.ModelDetailRep | null>(null);

const openCreate = () => {
  editingModel.value = null;
  dialogProvider.value = '';
  dialogOpen.value = true;
};

const openEdit = async (row: api.ModelPageRep) => {
  try {
    const detail = await api.GetDetail(row.id);
    editingModel.value = detail || null;
    dialogProvider.value = row.provider;
    dialogOpen.value = true;
  } catch {
    message.error('模型详情加载失败');
  }
};

const onDialogSuccess = () => {
  dialogOpen.value = false;
  load();
};

// ==================== 启停用 / 删除（管理端） ====================
const onToggle = async (row: api.ModelPageRep) => {
  try {
    await api.ToggleStatus(row.id, !row.status);
    message.success(row.status ? '已停用' : '已启用');
    load();
  } catch {
    message.error('状态更新失败');
  }
};

const onDelete = (row: api.ModelPageRep) => {
  Modal.confirm({
    title: '删除模型',
    content: `确认删除模型「${row.name}」？历史待审批申请将一并拒绝。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      await api.DelObj(row.id);
      message.success('删除成功');
      load();
    },
  });
};

// ==================== 申请使用 ====================
const applyVisible = ref(false);
const applyRow = ref<api.ModelPageRep | null>(null);
const applyReason = ref('');

const openApply = (row: api.ModelPageRep) => {
  applyRow.value = row;
  applyReason.value = '';
  applyVisible.value = true;
};

const submitApply = async () => {
  if (!applyRow.value) return;
  try {
    await api.ApplyModel(applyRow.value.id, applyReason.value);
    message.success('申请已提交，等待管理员审批');
    applyVisible.value = false;
    load();
  } catch (error: any) {
    message.error(error?.message || '申请提交失败');
  }
};

// ==================== 查看教程 ====================
const tutorialVisible = ref(false);
const tutorialMd = ref('');
const tutorialRow = ref<api.ModelPageRep | null>(null);

const openTutorial = async (row: api.ModelPageRep) => {
  tutorialVisible.value = true;
  tutorialRow.value = row;
  tutorialMd.value = '';
  try {
    const detail = await api.GetDetail(row.id);
    tutorialMd.value = detail?.tutorial_md || '暂无教程';
  } catch {
    tutorialMd.value = '教程加载失败';
  }
};

/** 下载教程为 Markdown 文件（文件名带模型名） */
const downloadTutorial = () => {
  const content = tutorialMd.value;
  if (!content || content === '暂无教程' || content === '教程加载失败') {
    message.warning('暂无教程内容可下载');
    return;
  }
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${tutorialRow.value?.name || '模型'}-使用教程.md`;
  a.click();
  // 延迟释放，避免部分浏览器在下载开始前 URL 失效
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
};

// ==================== 我的 API Key ====================
const keyVisible = ref(false);
const keyLoading = ref(false);
const myKeys = ref<api.MyKeyRep[]>([]);

const openMyKeys = async (row: api.ModelPageRep) => {
  keyVisible.value = true;
  keyLoading.value = true;
  try {
    const list = await api.GetMyKeys();
    myKeys.value = (list || []).filter((k) => k.model_id === row.id);
  } finally {
    keyLoading.value = false;
  }
};

/** 网关地址兜底：未配置时展示当前环境网关的 /api/model */
const defaultGatewayUrl = computed(() => {
  const baseUrl = import.meta.env.VITE_GLOB_API_URL || '';
  return resolveApiUrl('/api/model', baseUrl);
});

/** 我的 API Key 弹窗：非直连模型的支持后缀表格列 */
const suffixColumns = [
  { title: '后缀 URI', dataIndex: 'url', key: 'url' },
  { title: '接口说明', dataIndex: 'desc', key: 'desc' },
];

// ==================== 初始化 ====================
onMounted(async () => {
  try {
    const data: any = await api.GetCategories();
    categoryOptions.value = data?.categories || [];
  } catch {
    message.error('字典加载失败');
  }
  load();
});
</script>

<template>
  <div class="model-plaza-page page-layout-card">
    <div class="model-plaza-layout">
      <!-- 右侧：模型卡片网格 -->
      <section class="model-panel">
        <div class="model-panel__toolbar">
          <a-input-search
            v-model:value="query.name"
            placeholder="输入模型名称搜索"
            allow-clear
            style="width: 240px"
            @search="search"
          />
          <a-select
            v-model:value="query.category"
            placeholder="全部分类"
            allow-clear
            style="width: 160px"
            :options="categoryOptions"
            @change="search"
          />
          <a-button v-if="canAdd" type="primary" @click="openCreate">
            添加模型
          </a-button>
          <a-button @click="load">刷新</a-button>
        </div>

        <a-spin :spinning="loading">
          <a-row v-if="modelList.length > 0" :gutter="[12, 12]">
            <a-col
              v-for="model in modelList"
              :key="model.id"
              :xs="24"
              :sm="12"
              :lg="8"
              :xl="6"
            >
              <ModelCard
                :model="model"
                :can-edit="canEdit"
                :can-delete="canDelete"
                @apply="openApply"
                @tutorial="openTutorial"
                @my-key="openMyKeys"
                @edit="openEdit"
                @toggle="onToggle"
                @delete="onDelete"
              />
            </a-col>
          </a-row>
          <a-empty v-else description="暂无模型，点击右上角添加第一个模型" />
        </a-spin>

        <div class="model-panel__pagination">
          <a-pagination
            v-model:current="query.current"
            :page-size="query.size"
            :total="total"
            show-size-changer
            :page-size-options="['10', '20', '50', '100']"
            show-total
            @change="load"
            @show-size-change="
              (p: number, s: number) => {
                query.current = p;
                query.size = s;
                load();
              }
            "
          />
        </div>
      </section>
    </div>

    <!-- 添加/编辑模型弹窗（保留 maxkb 数据结构设计） -->
    <CreateModelDialog
      v-model:open="dialogOpen"
      :provider="dialogProvider"
      :model="editingModel"
      @success="onDialogSuccess"
    />

    <!-- 申请使用 -->
    <a-modal
      v-model:open="applyVisible"
      title="申请使用模型"
      width="480px"
      @ok="submitApply"
    >
      <a-form layout="vertical">
        <a-form-item label="模型">
          <a-input :value="applyRow?.name" disabled />
        </a-form-item>
        <a-form-item label="申请理由">
          <a-textarea
            v-model:value="applyReason"
            :rows="3"
            placeholder="请简要说明使用场景（可选）"
          />
        </a-form-item>
        <a-alert
          type="info"
          show-icon
          message="提交后需管理员审批，审批通过后可查看 API Key"
        />
      </a-form>
    </a-modal>

    <!-- 使用教程（Markdown 在线查看 + 下载） -->
    <a-modal v-model:open="tutorialVisible" title="使用教程" width="760px">
      <div class="model-tutorial">
        <MdPreview :model-value="tutorialMd" />
      </div>
      <template #footer>
        <a-space>
          <a-button @click="tutorialVisible = false">关闭</a-button>
          <a-button type="primary" @click="downloadTutorial">
            下载教程
          </a-button>
        </a-space>
      </template>
    </a-modal>

    <!-- 我的 API Key -->
    <a-modal
      v-model:open="keyVisible"
      title="我的 API Key"
      width="720px"
      :footer="null"
    >
      <a-spin :spinning="keyLoading">
        <template v-if="myKeys.length > 0">
          <a-card
            v-for="k in myKeys"
            :key="k.apply_id"
            size="small"
            style="margin-bottom: 12px"
          >
            <a-descriptions
              :column="1"
              size="small"
              :title="`${k.name}（${k.category_label} / ${k.provider_label}）`"
            >
              <a-descriptions-item label="模型标识">
                {{ k.model_name }}
              </a-descriptions-item>
              <a-descriptions-item label="接口地址">
                <a-typography-text code>
                  {{ k.gateway_url || defaultGatewayUrl }}
                </a-typography-text>
              </a-descriptions-item>
              <!-- 非直连模型：支持后缀表格（后缀 URI + 中文说明） -->
              <a-descriptions-item
                v-if="k.is_direct === false && (k.suffixes || []).length > 0"
                label="支持后缀"
              >
                <a-table
                  :columns="suffixColumns"
                  :data-source="k.suffixes"
                  :pagination="false"
                  row-key="url"
                  size="small"
                  :show-header="false"
                  class="my-key-suffix-table"
                >
                  <template #bodyCell="{ column, record }">
                    <template v-if="column.key === 'url'">
                      <a-typography-text code>
                        {{ record.url }}
                      </a-typography-text>
                    </template>
                    <template v-else-if="column.key === 'desc'">
                      {{ record.desc || '-' }}
                    </template>
                  </template>
                </a-table>
              </a-descriptions-item>
              <a-descriptions-item label="API Key">
                <a-typography-text code copyable style="color: #cf1322">
                  {{ k.api_key }}
                </a-typography-text>
              </a-descriptions-item>
              <a-descriptions-item label="限流">
                并发 {{ k.rate_limit_qps || '不限' }}
              </a-descriptions-item>
            </a-descriptions>
            <div v-if="k.tutorial_md" style="margin-top: 8px">
              <MdPreview :model-value="k.tutorial_md" />
            </div>
          </a-card>
        </template>
        <a-empty v-else description="暂无已通过的申请" />
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.model-plaza-page {
  padding: 12px;
}

.model-plaza-layout {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

/* ==================== 模型面板 ==================== */
.model-panel {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 6px;
  border: 1px solid rgba(5, 5, 5, 0.06);
  padding: 12px;
}

.model-panel__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.model-panel__pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.model-tutorial {
  max-height: 60vh;
  overflow-y: auto;
  padding: 4px;
}

/* 支持后缀表格：压缩单元格上下内边距，使首行 URI 与「支持后缀」label 顶部对齐 */
:deep(.my-key-suffix-table .ant-table-cell) {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
}
</style>
