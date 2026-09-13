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
import KeyBigScreen from './components/KeyBigScreen.vue';
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

// ==================== 我的 Key：下载 / 大屏 ====================
const bigScreenOpen = ref(false);
const bigScreenKey = ref<api.MyKeyRep | null>(null);

const openBigScreen = (k: api.MyKeyRep) => {
  bigScreenKey.value = k;
  bigScreenOpen.value = true;
};

/** 导出单个 Key 接入信息为 Markdown 文件（文件名带模型名） */
const downloadKey = (k: api.MyKeyRep) => {
  const paramRows = (k.common_params || [])
    .map(
      (p) => `| ${p.name} | ${p.default ?? '-'} | ${p.desc || '-'} | ${p.type} |`,
    )
    .join('\n');
  const md = [
    `# ${k.name}`,
    '',
    `- 能力类型：${k.category_label}`,
    `- 提供商：${k.provider_label}`,
    `- 模型标识：${k.model_name}`,
    `- 网关地址：${k.gateway_url || defaultGatewayUrl.value}`,
    `- API Key：${k.api_key}`,
    `- 限流：${k.rate_limit_qps ? '并发 ' + k.rate_limit_qps : '不限制'}`,
    `- 支持流式：${k.supports_stream ? '是' : '否'}（开启参数：${k.stream_param || 'stream'}）`,
    `- 支持深度思考：${k.supports_thinking ? '是' : '否'}（开启参数：${k.thinking_param || '-'}）`,
    k.tutorial_md ? `\n## 使用教程\n\n${k.tutorial_md}` : '',
    paramRows
      ? `\n## 常用参数\n\n| 参数名 | 默认值 | 说明 | 类型 |\n| --- | --- | --- | --- |\n${paramRows}`
      : '',
  ]
    .filter(Boolean)
    .join('\n');
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${k.name}-接入信息.md`;
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
              <a-descriptions-item label="API Key">
                <a-typography-text code copyable style="color: #cf1322">
                  {{ k.api_key }}
                </a-typography-text>
              </a-descriptions-item>
              <a-descriptions-item label="限流">
                并发 {{ k.rate_limit_qps || '不限' }}
              </a-descriptions-item>
              <a-descriptions-item label="能力">
                流式 {{ k.supports_stream ? '支持' : '不支持' }}（参数
                {{ k.stream_param || 'stream' }}）· 深度思考
                {{ k.supports_thinking ? '支持' : '不支持' }}（参数
                {{ k.thinking_param || '-' }}）· 常用参数
                {{ (k.common_params || []).length }} 个
              </a-descriptions-item>
            </a-descriptions>
            <div style="margin-top: 8px; text-align: right">
              <a-space>
                <a-button size="small" @click="downloadKey(k)">下载</a-button>
                <a-button size="small" type="primary" @click="openBigScreen(k)">
                  大屏查看
                </a-button>
              </a-space>
            </div>
            <div v-if="k.tutorial_md" style="margin-top: 8px">
              <MdPreview :model-value="k.tutorial_md" />
            </div>
          </a-card>
        </template>
        <a-empty v-else description="暂无已通过的申请" />
      </a-spin>
    </a-modal>

    <!-- 大屏查看我的 Key -->
    <KeyBigScreen v-model:open="bigScreenOpen" :key-data="bigScreenKey" />
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
</style>
