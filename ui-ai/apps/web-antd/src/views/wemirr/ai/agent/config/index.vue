<script lang="ts" setup name="ChatAgentPage">
/**
 * 智能体配置页面
 * 卡片式展示智能体列表，支持新增、编辑、删除、查看
 */
import { nextTick, onErrorCaptured, onMounted, watch } from 'vue';
import { useRouter } from 'vue-router';

import { RobotOutlined } from '@ant-design/icons-vue';
import { useFs } from '@fast-crud/fast-crud';
import { message, Modal } from 'ant-design-vue';

import AgentCard from './components/AgentCard.vue';
import createCrudOptions from './crud';

const router = useRouter();

const { crudBinding, crudRef, crudExpose } = useFs({
  createCrudOptions,
  context: {
    permission: 'ai:agent',
  },
});

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

function openEdit(item: any) {
  const index = crudBinding.value.data?.indexOf(item);
  crudExpose.openEdit({ row: item, index });
}

function openView(item: any) {
  const index = crudBinding.value.data?.indexOf(item);
  crudExpose.openView({ row: item, index });
}

function doRemove(item: any) {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除智能体「${item.name}」吗？删除后无法恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      const index = crudBinding.value.data?.indexOf(item);
      await crudExpose.doRemove({ row: item, index });
      message.success('删除成功');
    },
  });
}

/** 跳转到对话页面 */
function goToChat(_item: any) {
  router.push('/agent/chat');
}
</script>

<template>
  <fs-page class="page-layout-card agent-page">
    <fs-crud ref="crudRef" v-bind="crudBinding">
      <!-- 卡片列表 -->
      <div class="card-list-wrapper">
        <!-- 有数据时显示卡片网格 -->
        <div v-if="crudBinding.data?.length" class="card-grid">
          <AgentCard
            v-for="item of crudBinding.data"
            :key="item.id"
            :item="item"
            @view="openView"
            @edit="openEdit"
            @remove="doRemove"
            @chat="goToChat"
          />
        </div>

        <!-- 空状态 -->
        <div v-else class="empty-state">
          <div class="empty-icon">
            <RobotOutlined />
          </div>
          <h3 class="empty-title">还没有智能体</h3>
          <p class="empty-desc">创建你的第一个智能体，开启 AI 对话之旅</p>
          <a-button type="primary" size="large" @click="crudExpose.openAdd({})">
            <template #icon><RobotOutlined /></template>
            创建智能体
          </a-button>
        </div>
      </div>
    </fs-crud>
  </fs-page>
</template>

<style lang="less" scoped>
@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgb(114 46 209 / 20%);
    transform: scale(1);
  }

  50% {
    box-shadow: 0 0 0 15px rgb(114 46 209 / 0%);
    transform: scale(1.02);
  }
}

.agent-page {
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
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
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
    color: #722ed1;
    background: linear-gradient(135deg, rgb(114 46 209 / 10%) 0%, rgb(24 144 255 / 10%) 100%);
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
