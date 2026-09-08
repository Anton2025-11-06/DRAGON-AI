<script lang="ts">
/**
 * 工作流执行历史页面
 * 展示工作流执行记录，支持查看详情
 */
import type { WorkflowExecutionResp } from '#/api/ai-workflow/types';

import { computed, defineComponent, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { ArrowLeftOutlined, HistoryOutlined } from '@ant-design/icons-vue';
import { useFs } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';
import { getExecution, getWorkflowDetail } from '#/api/ai-workflow';

import ExecutionDetailModal from './components/ExecutionDetailModal.vue';
import createCrudOptions from './crud';

export default defineComponent({
  name: 'WorkflowHistoryPage',
  components: {
    ExecutionDetailModal,
  },
  setup() {
    const route = useRoute();
    const router = useRouter();

    // crud 组件的 ref
    const crudRef = ref();
    // crud 配置的 ref
    const crudBinding = ref();

    // 工作流ID
    const workflowId = computed(() => {
      const id = route.params.id;
      return id ? String(id) : null;
    });

    // 工作流名称
    const workflowName = ref('');

    // 详情弹窗
    const detailModalVisible = ref(false);
    const selectedExecution = ref<WorkflowExecutionResp>();

    // 加载工作流信息
    async function loadWorkflowInfo() {
      if (workflowId.value) {
        try {
          const workflow = await getWorkflowDetail(workflowId.value);
          workflowName.value = workflow.name;
        } catch {
          message.error('加载工作流信息失败');
        }
      }
    }

    /** 返回（需求 4.3：始终回到工作流列表页，不再回到编辑页） */
    function handleBack() {
      router.push('/agent/workflow');
    }

    /** 打开详情弹窗 */
    async function openDetail(row: WorkflowExecutionResp) {
      try {
        // 调用详情接口获取完整数据（包含 inputs, outputs, nodeStates 等大字段）
        const detail = await getExecution(row.executionId);
        selectedExecution.value = detail;
        detailModalVisible.value = true;
      } catch (error: any) {
        message.error(`获取执行详情失败: ${error.message || '未知错误'}`);
      }
    }

    onMounted(async () => {
      const { crudExpose } = useFs({
        crudBinding,
        crudRef,
        createCrudOptions,
        context: {
          permission: 'ai:workflow:execution',
          workflowId: workflowId.value,
          openDetail,
        },
      });

      await loadWorkflowInfo();
      await crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      workflowName,
      handleBack,
      detailModalVisible,
      selectedExecution,
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card workflow-history-page">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding">
      <!-- 自定义标题栏 -->
      <template #actionbar-left>
        <div class="page-header">
          <a-button type="text" @click="handleBack">
            <template #icon><ArrowLeftOutlined /></template>
          </a-button>
          <HistoryOutlined class="header-icon" />
          <span class="header-title">
            {{ workflowName ? `${workflowName} - 执行历史` : '执行历史' }}
          </span>
        </div>
      </template>
    </fs-crud>

    <!-- 执行详情弹窗 -->
    <ExecutionDetailModal
      v-model:open="detailModalVisible"
      :execution="selectedExecution"
    />
  </fs-page>
</template>

<style lang="less" scoped>
.workflow-history-page {
  .page-header {
    display: flex;
    gap: 8px;
    align-items: center;

    .header-icon {
      font-size: 18px;
      color: #1890ff;
    }

    .header-title {
      font-size: 16px;
      font-weight: 500;
      color: #333;
    }
  }
}
</style>
