<script lang="ts" setup name="VersionHistoryModal">
/**
 * 工作流发布历史弹窗
 * 查看历史发布版本与时间；每个版本可「恢复此版本」（回滚并重新发布为新版本）
 */
import type { WorkflowVersionResp } from '#/api/ai-workflow/types';

import { ref, watch } from 'vue';

import { RollbackOutlined } from '@ant-design/icons-vue';
import { message, Modal } from 'ant-design-vue';

import { getWorkflowVersionHistory, rollbackWorkflow } from '#/api/ai-workflow';

interface Props {
  open: boolean;
  workflowId: string;
  currentVersion?: number;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  /** 恢复版本成功（父级需重新加载工作流以展示该版本内容） */
  (e: 'restored', version: number): void;
}>();

const loading = ref(false);
const restoring = ref(false);
const versions = ref<WorkflowVersionResp[]>([]);

const columns = [
  { title: '版本', key: 'version', width: 110 },
  { title: '发布时间', dataIndex: 'createdTime', key: 'createdTime', width: 180 },
  { title: '变更说明', dataIndex: 'changeLog', key: 'changeLog' },
  { title: '操作', key: 'action', width: 120, fixed: 'right' as const },
];

async function loadVersions() {
  if (!props.workflowId) return;
  loading.value = true;
  try {
    versions.value = await getWorkflowVersionHistory(props.workflowId);
  } catch {
    message.error('加载发布历史失败');
  } finally {
    loading.value = false;
  }
}

/** 恢复指定版本：回滚并重新发布为新版本，随后父级刷新展示 */
function handleRestore(record: WorkflowVersionResp) {
  Modal.confirm({
    title: `恢复版本 v${record.version}`,
    content:
      `将把 v${record.version} 的内容恢复为当前工作流，并立即发布为最新版本（历史版本保留）。确定继续吗？`,
    okText: '恢复',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      restoring.value = true;
      try {
        await rollbackWorkflow(props.workflowId, record.version);
        message.success(`已恢复 v${record.version}，并发布为最新版本`);
        emit('update:open', false);
        emit('restored', record.version);
      } catch {
        message.error('恢复失败');
      } finally {
        restoring.value = false;
      }
    },
  });
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      loadVersions();
    }
  },
);
</script>

<template>
  <a-modal
    :open="open"
    :width="720"
    title="发布历史"
    :footer="null"
    @cancel="emit('update:open', false)"
  >
    <a-table
      :columns="columns"
      :data-source="versions"
      :loading="loading"
      :pagination="false"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <a-space>
            <span class="version-tag">v{{ record.version }}</span>
            <a-tag
              v-if="record.version === currentVersion"
              color="green"
              style="margin-left: 0"
            >
              当前版本
            </a-tag>
          </a-space>
        </template>
        <template v-else-if="column.key === 'changeLog'">
          <span class="change-log">{{ record.changeLog || '-' }}</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button
            type="link"
            size="small"
            :disabled="record.version === currentVersion || restoring"
            @click="handleRestore(record)"
          >
            <template #icon><RollbackOutlined /></template>
            {{ record.version === currentVersion ? '当前版本' : '恢复此版本' }}
          </a-button>
        </template>
      </template>
    </a-table>

    <a-empty
      v-if="!loading && versions.length === 0"
      description="暂无发布记录，发布后此处会生成版本快照"
    />
  </a-modal>
</template>

<style lang="less" scoped>
.version-tag {
  font-weight: 600;
  color: #1890ff;
}

.change-log {
  display: -webkit-box;
  overflow: hidden;
  font-size: 13px;
  color: #666;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>