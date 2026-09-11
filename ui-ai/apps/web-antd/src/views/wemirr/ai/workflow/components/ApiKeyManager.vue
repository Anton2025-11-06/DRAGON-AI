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
  PlusOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import {
  createApiKey,
  deleteApiKey,
  listApiKeys,
  updateApiKeyStatus,
} from '#/api/ai-workflow';

interface Props {
  workflowId: number | string;
}

const props = defineProps<Props>();

const loading = ref(false);
const creating = ref(false);
const apiKeys = ref<ApiKeyListResp[]>([]);
const showCreateModal = ref(false);
const showKeyModal = ref(false);
const newApiKey = ref('');

const apiBaseUrl = `${window.location.origin}/api/workflow`;

const createForm = reactive({
  name: '',
  rateLimit: 0,
  expireDays: 0,
});

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

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 120 },
  {
    title: 'API Key',
    dataIndex: 'apiKey',
    key: 'apiKey',
    width: 260,
  },
  { title: '状态', key: 'status', width: 80 },
  { title: 'QPS', key: 'rateLimit', width: 80 },
  { title: '调用次数', key: 'totalCalls', width: 100 },
  {
    title: '最后使用',
    dataIndex: 'lastUsedTime',
    key: 'lastUsedTime',
    width: 160,
  },
  { title: '创建时间', dataIndex: 'createTime', key: 'createTime', width: 160 },
  { title: '操作', key: 'action', width: 140, fixed: 'right' as const },
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

    // 显示新建的 key
    newApiKey.value = resp.apiKey;
    showCreateModal.value = false;
    showKeyModal.value = true;

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

function handleCopy() {
  navigator.clipboard
    .writeText(newApiKey.value)
    .then(() => {
      message.success('已复制到剪贴板');
    })
    .catch(() => {
      message.error('复制失败，请手动复制');
    });
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

    <a-alert type="info" show-icon style="margin-bottom: 16px">
      <template #message>
        第三方系统可通过 API Key 执行此工作流（需先发布），请求头添加
        <code style="word-break: break-all">X-Workflow-Token: {{ newApiKey }}</code>，鉴权与限流由网关统一执行；
        限流按 API Key 的 QPS 限制，0 表示不限制。
      </template>
    </a-alert>

    <!-- API Key 列表 -->
    <a-table
      :columns="columns"
      :data-source="apiKeys"
      :loading="loading"
      :pagination="false"
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
          <a-tag :color="record.status === 'ACTIVE' ? 'green' : 'default'">
            {{ record.status === 'ACTIVE' ? '启用' : '禁用' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'rateLimit'">
          {{ record.rateLimit > 0 ? `${record.rateLimit} QPS` : '不限制' }}
        </template>
        <template v-else-if="column.key === 'totalCalls'">
          {{ record.totalCalls.toLocaleString() }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
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

    <!-- 显示新创建的 API Key 弹窗 -->
    <a-modal
      v-model:open="showKeyModal"
      title="API Key 创建成功"
      :footer="null"
      :closable="true"
      :width="640"
    >
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 16px"
        message="API Key 已生成，可在列表中随时查看完整内容并复制。"
      />
      <div class="key-display">
        <code class="key-value">{{ newApiKey }}</code>
        <a-button type="primary" size="small" @click="handleCopy">
          <CopyOutlined /> 复制
        </a-button>
      </div>

      <a-divider style="margin: 16px 0" />

      <h4>调用示例</h4>
      <div class="code-block">
        <pre><code># 1. 异步执行工作流（返回 executionId）
curl -X POST {{ apiBaseUrl }}/workflow-executions/workflows/{{ props.workflowId }}/execute-async \
  -H "X-Workflow-Token: {{ newApiKey }}" \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"query": "你好"}}'

# 2. 订阅执行事件流（SSE 长连接，网关白名单放行，无需携带请求头）
curl -N {{ apiBaseUrl }}/workflow-executions/&lt;executionId&gt;/subscribe

# 事件类型：node.started / node.completed / node.failed / node.delta
#          / workflow.paused / workflow.completed / workflow.failed / workflow.cancelled</code></pre>
      </div>
    </a-modal>
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

  .key-display {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    padding: 12px;
    background: #f5f5f5;
    border-radius: 6px;

    .key-value {
      flex: 1;
      min-width: 0;
      font-size: 13px;
      word-break: break-all;
    }
  }

  .key-cell {
    font-size: 12px;
    word-break: break-all;
  }

  .code-block {
    padding: 12px;
    background: #1e1e1e;
    border-radius: 6px;
    overflow-x: auto;

    pre {
      margin: 0;

      code {
        color: #d4d4d4;
        font-size: 12px;
        white-space: pre-wrap;
        word-break: break-all;
      }
    }
  }
}
</style>
