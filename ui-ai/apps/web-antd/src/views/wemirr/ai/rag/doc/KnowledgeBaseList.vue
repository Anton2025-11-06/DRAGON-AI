<script lang="ts" setup>
/**
 * 知识库列表组件
 * 使用 fast-crud 管理知识库的新增/编辑/删除
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import {
  ApartmentOutlined,
  BookOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { useFs } from '@fast-crud/fast-crud';
import {
  Button,
  Card,
  message,
  Modal,
  Space,
  Spin,
  Tooltip,
} from 'ant-design-vue';

import createKnowledgeBaseCrud from './knowledge-base-crud';

const emit = defineEmits<{
  (e: 'select', kb: any): void;
  (e: 'openGraph', kb: any): void;
  (e: 'openRecallTest', kb: any): void;
  (e: 'openSemanticSearch', kb: any): void;
}>();

const route = useRoute();
const searchText = ref('');
const selectedKeys = ref<number[]>([]);

// 初始化 fast-crud（隐藏表格，只用表单）
const { crudBinding, crudRef, crudExpose } = useFs({
  createCrudOptions: createKnowledgeBaseCrud,
});

// 过滤后的知识库列表
const filteredKnowledgeBases = computed(() => {
  const list = crudBinding.value.data || [];
  if (!searchText.value.trim()) return list;
  const keyword = searchText.value.toLowerCase().trim();
  return list.filter(
    (kb: any) =>
      kb.name?.toLowerCase().includes(keyword) ||
      kb.description?.toLowerCase().includes(keyword),
  );
});

// 加载状态
const loading = computed(() => crudBinding.value.table?.loading);

// 选择知识库
const handleSelect = (kb: any) => {
  selectedKeys.value = [kb.id];
  emit('select', kb);
};

// 新增知识库
const handleAdd = () => {
  crudExpose.openAdd({});
};

// 编辑知识库
const handleEdit = (kb: any, event: Event) => {
  event.stopPropagation();
  const index = crudBinding.value.data?.indexOf(kb);
  crudExpose.openEdit({ row: kb, index });
};

// 删除知识库
const handleDelete = (kb: any, event: Event) => {
  event.stopPropagation();
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除知识库「${kb.name}」吗？删除后将无法恢复。`,
    okType: 'danger',
    onOk: async () => {
      const index = crudBinding.value.data?.indexOf(kb);
      await crudExpose.doRemove({ row: kb, index }, { noConfirm: true });
      message.success('删除成功');
      // 如果删除的是当前选中的，清空选中
      if (selectedKeys.value.includes(kb.id)) {
        selectedKeys.value = [];
        emit('select', null);
      }
    },
  });
};

// 打开图谱可视化
const handleOpenGraph = (kb: any, event: Event) => {
  event.stopPropagation();
  emit('openGraph', kb);
};

// 打开实体召回测试
const handleOpenRecallTest = (kb: any, event: Event) => {
  event.stopPropagation();
  emit('openRecallTest', kb);
};

// 打开语义搜索
const handleOpenSemanticSearch = (kb: any, event: Event) => {
  event.stopPropagation();
  emit('openSemanticSearch', kb);
};

// 监听数据变化，自动选中
watch(
  () => crudBinding.value.data,
  (list) => {
    if (!list?.length) return;
    // 如果有路由参数，优先选中
    const kbIdFromQuery = route.query.kbId;
    if (kbIdFromQuery) {
      const targetKb = list.find((kb: any) => kb.id === Number(kbIdFromQuery));
      if (targetKb && !selectedKeys.value.includes(targetKb.id)) {
        handleSelect(targetKb);
        return;
      }
    }
    // 如果没有选中项，默认选中第一个
    if (selectedKeys.value.length === 0) {
      handleSelect(list[0]);
    }
  },
);

// 暴露刷新方法
defineExpose({
  refresh: () => crudExpose.doRefresh(),
});

onMounted(() => {
  crudExpose.doRefresh();
});
</script>

<template>
  <div class="kb-wrapper">
    <Card class="kb-card" :bordered="false">
      <template #title>
        <div class="flex items-center">
          <DatabaseOutlined class="mr-2 text-blue-500" />
          知识库列表
        </div>
      </template>
      <template #extra>
        <Tooltip title="新增知识库">
          <Button type="primary" size="small" @click="handleAdd">
            <template #icon><PlusOutlined /></template>
          </Button>
        </Tooltip>
      </template>

      <div class="kb-content">
        <!-- 搜索框 -->
        <div class="mb-4">
          <a-input-search
            v-model:value="searchText"
            placeholder="搜索知识库..."
            allow-clear
          />
        </div>

        <!-- 知识库列表 -->
        <div class="kb-list">
          <Spin :spinning="loading">
            <div
              v-for="kb in filteredKnowledgeBases"
              :key="kb.id"
              class="kb-item"
              :class="{ selected: selectedKeys.includes(kb.id) }"
              @click="handleSelect(kb)"
            >
              <div class="kb-header">
                <div class="flex flex-1 items-center">
                  <BookOutlined class="mr-2 text-blue-500" />
                  <span class="font-medium">{{ kb.name }}</span>
                </div>
                <Space :size="0" class="kb-actions">
                  <Tooltip title="编辑">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleEdit(kb, e)"
                    >
                      <template #icon><EditOutlined /></template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="删除">
                    <Button
                      type="text"
                      size="small"
                      danger
                      @click="(e) => handleDelete(kb, e)"
                    >
                      <template #icon><DeleteOutlined /></template>
                    </Button>
                  </Tooltip>
                </Space>
              </div>

              <div v-if="kb.description" class="kb-description">
                {{ kb.description }}
              </div>

              <div class="kb-meta">
                <a-tag size="small" color="blue">
                  TopK: {{ kb.topK || 5 }}
                </a-tag>
                <a-tag size="small" color="green">
                  阈值: {{ kb.scoreThreshold || 0.6 }}
                </a-tag>
              </div>

              <div class="kb-footer">
                <span class="kb-time">{{ kb.createTime }}</span>
                <Space :size="4">
                  <Tooltip title="语义搜索">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleOpenSemanticSearch(kb, e)"
                    >
                      <template #icon><SearchOutlined /></template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="实体召回">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleOpenRecallTest(kb, e)"
                    >
                      <template #icon><ClusterOutlined /></template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="知识图谱">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleOpenGraph(kb, e)"
                    >
                      <template #icon><ApartmentOutlined /></template>
                    </Button>
                  </Tooltip>
                </Space>
              </div>
            </div>

            <!-- 空状态 -->
            <div
              v-if="filteredKnowledgeBases.length === 0 && !loading"
              class="empty-state"
            >
              <DatabaseOutlined class="empty-icon" />
              <p v-if="searchText.trim()">
                未找到包含"{{ searchText }}"的知识库
              </p>
              <div v-else>
                <p>暂无知识库</p>
                <Button type="link" @click="handleAdd">立即创建</Button>
              </div>
            </div>
          </Spin>
        </div>
      </div>
    </Card>

    <!-- 隐藏的 fs-crud，只用于表单弹窗 -->
    <fs-crud ref="crudRef" v-bind="crudBinding" class="hidden-crud" />
  </div>
</template>

<style lang="less" scoped>
.kb-wrapper {
  position: relative;
  height: 100%;
}

.kb-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;

  :deep(.ant-card-body) {
    display: flex;
    flex: 1;
    flex-direction: column;
    padding: 16px;
    overflow: hidden;
  }
}

.kb-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}

.kb-list {
  flex: 1;
  padding-right: 4px;
  overflow-y: auto;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-thumb {
    background: #ccc;
    border-radius: 4px;
  }
}

.kb-item {
  padding: 12px;
  margin-bottom: 8px;
  cursor: pointer;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color, #e8e8e8);
  border-radius: 6px;
  transition: all 0.3s;

  &:hover {
    border-color: hsl(var(--primary));
    box-shadow: 0 2px 8px rgb(0 0 0 / 10%);

    .kb-actions {
      opacity: 1;
    }
  }

  &.selected {
    background: hsl(var(--primary) / 10%);
    border-color: hsl(var(--primary));
  }
}

.kb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.kb-actions {
  opacity: 0;
  transition: opacity 0.2s;
}

.kb-description {
  display: -webkit-box;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-color-secondary, #666);
  -webkit-box-orient: vertical;
}

.kb-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.kb-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kb-time {
  font-size: 11px;
  color: var(--text-color-secondary, #999);
}

.empty-state {
  padding: 40px 20px;
  color: var(--text-color-secondary, #999);
  text-align: center;

  .empty-icon {
    margin-bottom: 16px;
    font-size: 48px;
    opacity: 0.3;
  }
}

// 完全隐藏 fs-crud（弹窗会挂载到 body，不受影响）
.hidden-crud {
  display: none;
}
</style>
