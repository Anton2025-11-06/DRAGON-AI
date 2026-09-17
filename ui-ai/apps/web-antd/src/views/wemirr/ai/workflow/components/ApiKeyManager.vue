<script setup lang="ts">
/**
 * 工作流 API Key 管理组件
 * 用于在工作流详情中管理第三方访问凭证
 */
import type { ApiKeyListResp } from '#/api/ai-workflow';

import { onMounted, reactive, ref } from 'vue';

import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import {
  createApiKey,
  deleteApiKey,
  listApiKeys,
  updateApiKey,
  updateApiKeyStatus,
} from '#/api/ai-workflow';

import ApiKeyUsageModal from './ApiKeyUsageModal.vue';

interface Props {
  workflowId: number | string;
}

const props = defineProps<Props>();

const loading = ref(false);
const creating = ref(false);
const apiKeys = ref<ApiKeyListResp[]>([]);
const showCreateModal = ref(false);
/** 用法说明弹窗：创建成功后自动打开，列表「查看」随时可再看 */
const showUsageModal = ref(false);
const usageApiKey = ref('');
/** 编辑弹窗（重命名 / 改 QPS / 改过期时间） */
const showEditModal = ref(false);
const editing = ref(false);
const editForm = reactive({
  expireTime: undefined as string | undefined,
  id: 0,
  name: '',
  rateLimit: 0,
});

const createForm = reactive({
  name: '',
  rateLimit: 0,
  expireDays: 0,
});

/** 后端时间串规整到秒（历史数据可能带 T 分隔） */
function formatTime(time?: null | string): string {
  return time ? String(time).replace('T', ' ').slice(0, 19) : '';
}

/** 过期判定：状态已是 EXPIRED，或设了时间但还没被后端刷状态 */
function isExpired(record: ApiKeyListResp): boolean {
  if (record.status === 'EXPIRED') return true;
  return (
    !!record.expireTime && new Date(record.expireTime).getTime() < Date.now()
  );
}

// 过期是系统判定的结果，与用户主动停用分开显示，不能一律当成「禁用」
function statusText(record: ApiKeyListResp): string {
  if (record.status === 'ACTIVE') return '启用';
  return isExpired(record) ? '已过期' : '禁用';
}

function statusColor(record: ApiKeyListResp): string {
  if (record.status === 'ACTIVE') return 'green';
  return isExpired(record) ? 'orange' : 'default';
}

/** 复制某行 API Key */
function copyKey(key: string) {
  navigator.clipboard
    .writeText(key)
    .then(() => {
      message.success('已复制到剪贴板');
    })
    .catch(() => {
      message.error('复制失败，请手动复制');
    });
}

/**
 * 列定义：没列“调用次数 / 最后使用”——网关鉴权与限流只读 Redis，
 * 根本不会回写 total_calls / last_used_time，放出来永远是一片 0 和空。
 */
const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 110 },
  {
    title: 'API Key',
    dataIndex: 'apiKey',
    key: 'apiKey',
    width: 250,
  },
  { title: '状态', key: 'status', width: 80 },
  { title: 'QPS', key: 'rateLimit', width: 90 },
  { title: '过期时间', key: 'expireTime', width: 160 },
  { title: '创建时间', dataIndex: 'createTime', key: 'createTime', width: 160 },
  { title: '操作', key: 'action', width: 210, fixed: 'right' as const },
];

async function loadApiKeys() {
  loading.value = true;
  try {
    apiKeys.value = await listApiKeys(props.workflowId);
  } catch {
    message.error('加载 API 访问凭证失败');
  } finally {
    loading.value = false;
  }
}

async function handleCreate() {
  if (!createForm.name.trim()) {
    message.warning('请输入备注名称');
    return;
  }
  creating.value = true;
  try {
    const resp = await createApiKey({
      workflowId: props.workflowId,
      name: createForm.name.trim(),
      rateLimit: createForm.rateLimit || 0,
      expireDays: createForm.expireDays || undefined,
    });

    // 直接把用法说明拉开，省得用户还要回列表找刚建的那行
    usageApiKey.value = resp.apiKey;
    showCreateModal.value = false;
    showUsageModal.value = true;

    // 重置表单
    createForm.name = '';
    createForm.rateLimit = 0;
    createForm.expireDays = 0;

    // 刷新列表
    await loadApiKeys();
  } catch {
    message.error('创建失败');
  } finally {
    creating.value = false;
  }
}

async function handleToggleStatus(record: ApiKeyListResp, checked: boolean) {
  try {
    // 启用/禁用：仅支持 ACTIVE/REVOKED/EXPIRED（后端报错修复点：禁用状态传 REVOKED）
    await updateApiKeyStatus(record.id, checked ? 'ACTIVE' : 'REVOKED');
    message.success(checked ? '已启用' : '已禁用');
    await loadApiKeys();
  } catch {
    message.error('操作失败');
  }
}

async function handleDelete(id: number) {
  try {
    await deleteApiKey(id);
    message.success('删除成功');
    await loadApiKeys();
  } catch {
    message.error('删除失败');
  }
}

/** 打开某一行的用法说明 */
function openUsage(record: ApiKeyListResp) {
  usageApiKey.value = record.apiKey;
  showUsageModal.value = true;
}

function openEdit(record: ApiKeyListResp) {
  editForm.id = record.id;
  editForm.name = record.name;
  editForm.rateLimit = record.rateLimit ?? 0;
  editForm.expireTime = record.expireTime || undefined;
  showEditModal.value = true;
}

async function handleEdit() {
  if (!editForm.name.trim()) {
    message.warning('请输入备注名称');
    return;
  }
  editing.value = true;
  try {
    await updateApiKey(editForm.id, {
      name: editForm.name.trim(),
      rateLimit: editForm.rateLimit || 0,
      // 清空是“改为永不过期”，必须显式传 null；不传后端理解为不改过期时间
      expireTime: editForm.expireTime || null,
    });
    message.success('已保存');
    showEditModal.value = false;
    await loadApiKeys();
  } catch {
    message.error('保存失败');
  } finally {
    editing.value = false;
  }
}

onMounted(() => {
  loadApiKeys();
});
</script>

<template>
  <div class="api-key-manager">
    <!-- 头部操作栏 -->
    <div class="manager-header">
      <h4 style="margin: 0">API 访问凭证</h4>
      <a-button type="primary" size="small" @click="showCreateModal = true">
        <PlusOutlined /> 创建 API Key
      </a-button>
    </div>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 16px"
      message="第三方系统可通过 API Key 执行此工作流（需先发布）：请求头添加 X-Workflow-Token，鉴权与限流由网关统一执行；限流按 Key 配置的 QPS，0 表示不限制。完整调用地址与 curl 示例见每行的「查看」。"
    />

    <!-- API Key 列表 -->
    <a-table
      :columns="columns"
      :data-source="apiKeys"
      :loading="loading"
      :pagination="false"
      :scroll="{ x: 1060 }"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'apiKey'">
          <a-space>
            <code class="key-cell">{{ record.apiKey }}</code>
            <a-button type="link" size="small" @click="copyKey(record.apiKey)">
              <CopyOutlined />
            </a-button>
          </a-space>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColor(record)">
            {{ statusText(record) }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'rateLimit'">
          {{ record.rateLimit > 0 ? `${record.rateLimit} QPS` : '不限制' }}
        </template>
        <template v-else-if="column.key === 'expireTime'">
          <span v-if="!record.expireTime" class="expire-never">永不过期</span>
          <span v-else :class="{ 'expire-done': isExpired(record) }">
            {{ formatTime(record.expireTime) }}
          </span>
        </template>
        <template v-else-if="column.key === 'createTime'">
          {{ formatTime(record.createTime) || '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space :size="4">
            <a-button
              type="link"
              size="small"
              title="查看调用用法说明"
              @click="openUsage(record)"
            >
              <EyeOutlined /> 查看
            </a-button>
            <a-button type="link" size="small" @click="openEdit(record)">
              <EditOutlined /> 编辑
            </a-button>
            <a-switch
              :checked="record.status === 'ACTIVE'"
              checked-children="启用"
              un-checked-children="禁用"
              size="small"
              @change="
                (checked: boolean) => handleToggleStatus(record, checked)
              "
            />
            <a-popconfirm
              title="确定删除此 API Key？删除后第三方将无法使用此 Key 调用工作流。"
              ok-text="删除"
              cancel-text="取消"
              @confirm="handleDelete(record.id)"
            >
              <a-button type="link" danger size="small">
                <DeleteOutlined />
              </a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-empty
      v-if="!loading && apiKeys.length === 0"
      description="暂无 API Key，点击上方按钮创建"
    />

    <!-- 创建 API Key 弹窗 -->
    <a-modal
      v-model:open="showCreateModal"
      title="创建 API Key"
      @ok="handleCreate"
      :confirm-loading="creating"
    >
      <a-form layout="vertical" :model="createForm">
        <a-form-item label="备注名称" required>
          <a-input
            v-model:value="createForm.name"
            placeholder="例如：生产环境、测试联调"
          />
        </a-form-item>
        <a-form-item label="QPS 限制">
          <a-input-number
            v-model:value="createForm.rateLimit"
            :min="0"
            :max="1000"
            style="width: 100%"
            placeholder="0 表示不限制"
          />
        </a-form-item>
        <a-form-item label="有效期（天）">
          <a-input-number
            v-model:value="createForm.expireDays"
            :min="0"
            :max="3650"
            style="width: 100%"
            placeholder="0 或空表示永不过期"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 编辑 API Key：重命名 / 改 QPS / 改过期时间 -->
    <a-modal
      v-model:open="showEditModal"
      :confirm-loading="editing"
      title="编辑 API Key"
      @ok="handleEdit"
    >
      <a-form :model="editForm" layout="vertical">
        <a-form-item label="备注名称" required>
          <a-input
            v-model:value="editForm.name"
            :maxlength="128"
            placeholder="例如：生产环境、测试联调"
          />
        </a-form-item>
        <a-form-item label="QPS 限制">
          <a-input-number
            v-model:value="editForm.rateLimit"
            :max="1000"
            :min="0"
            placeholder="0 表示不限制"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="过期时间">
          <a-date-picker
            v-model:value="editForm.expireTime"
            format="YYYY-MM-DD HH:mm:ss"
            placeholder="留空表示永不过期"
            show-time
            style="width: 100%"
            value-format="YYYY-MM-DD HH:mm:ss"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 用法说明：刚创建与列表「查看」共用同一份模板 -->
    <ApiKeyUsageModal
      v-model:open="showUsageModal"
      :api-key="usageApiKey"
      :workflow-id="props.workflowId"
    />
  </div>
</template>

<style scoped lang="less">
.api-key-manager {
  padding: 16px;

  .manager-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }

  .key-cell {
    font-size: 12px;
    word-break: break-all;
  }

  // 过期时间是“没设”而不是“没数据”，两者语义不一样，不能统一显示 —
  .expire-never {
    color: #bfbfbf;
  }

  .expire-done {
    color: #ff4d4f;
  }
}
</style>
