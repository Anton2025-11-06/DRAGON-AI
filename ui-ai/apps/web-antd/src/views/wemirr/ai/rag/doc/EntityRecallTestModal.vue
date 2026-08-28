<script lang="ts" setup>
/**
 * 实体召回测试弹窗组件
 * 用于测试知识图谱的实体召回效果
 */
import { ref, watch } from 'vue';

import {
  DeleteOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Card,
  Empty,
  Input,
  List,
  message,
  Modal,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
} from 'ant-design-vue';

import type { EntityRecallResp } from './api';
import { TestEntityRecall } from './api';

const props = defineProps<{
  open: boolean;
  knowledgeBaseId: number | null;
  knowledgeBaseName?: string;
}>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
}>();

const loading = ref(false);
const keywords = ref<string[]>(['']);
const recallResult = ref<EntityRecallResp | null>(null);

// 表格列定义
const entityColumns = [
  {
    title: '实体名称',
    dataIndex: 'name',
    key: 'name',
    width: 200,
  },
  {
    title: '实体类型',
    dataIndex: 'type',
    key: 'type',
    width: 120,
  },
  {
    title: '匹配分数',
    dataIndex: 'score',
    key: 'score',
    width: 100,
    customRender: ({ value }: { value: number }) => {
      return value ? value.toFixed(4) : '-';
    },
  },
];

// 添加关键词
const addKeyword = () => {
  keywords.value.push('');
};

// 删除关键词
const removeKeyword = (index: number) => {
  if (keywords.value.length > 1) {
    keywords.value.splice(index, 1);
  }
};

// 更新关键词
const updateKeyword = (index: number, value: string) => {
  keywords.value[index] = value;
};

// 执行召回测试
const handleRecallTest = async () => {
  if (!props.knowledgeBaseId) {
    message.warning('请选择知识库');
    return;
  }

  // 过滤空关键词
  const validKeywords = keywords.value.filter((k) => k.trim());
  if (validKeywords.length === 0) {
    message.warning('请输入至少一个关键词');
    return;
  }

  loading.value = true;
  try {
    const result = await TestEntityRecall(props.knowledgeBaseId, validKeywords);
    recallResult.value = result;
    if (result.entityCount === 0) {
      message.info('未召回任何实体');
    } else {
      message.success(`成功召回 ${result.entityCount} 个实体`);
    }
  } catch (error) {
    console.error('召回测试失败:', error);
    message.error('召回测试失败');
  } finally {
    loading.value = false;
  }
};

// 清空结果
const clearResult = () => {
  recallResult.value = null;
  keywords.value = [''];
};

// 监听弹窗关闭时清空
watch(
  () => props.open,
  (val) => {
    if (!val) {
      clearResult();
    }
  },
);
</script>

<template>
  <Modal
    :open="open"
    :title="null"
    width="900px"
    :bodyStyle="{ maxHeight: '70vh', overflow: 'auto' }"
    :footer="null"
    :destroyOnClose="true"
    @cancel="emit('update:open', false)"
  >
    <template #title>
      <div class="flex items-center">
        <SearchOutlined class="mr-2 text-blue-500" />
        <span>{{ knowledgeBaseName || '知识库' }} - 图谱召回测试</span>
      </div>
    </template>

    <div class="recall-wrapper">
      <!-- 关键词输入区域 -->
      <Card title="输入关键词" size="small" class="mb-4">
        <div class="keyword-list">
          <div
            v-for="(keyword, index) in keywords"
            :key="index"
            class="keyword-item mb-2 flex items-center gap-2"
          >
            <Input
              :value="keyword"
              placeholder="请输入关键词"
              style="flex: 1"
              @update:value="(val: string) => updateKeyword(index, val)"
              @pressEnter="handleRecallTest"
            />
            <Button
              v-if="keywords.length > 1"
              type="text"
              danger
              size="small"
              @click="removeKeyword(index)"
            >
              <template #icon><DeleteOutlined /></template>
            </Button>
          </div>
        </div>

        <div class="mt-3 flex items-center justify-between">
          <Button type="dashed" size="small" @click="addKeyword">
            <template #icon><PlusOutlined /></template>
            添加关键词
          </Button>
          <Space>
            <Button @click="clearResult">清空</Button>
            <Button type="primary" :loading="loading" @click="handleRecallTest">
              <template #icon><SearchOutlined /></template>
              开始召回测试
            </Button>
          </Space>
        </div>
      </Card>

      <!-- 结果展示区域 -->
      <Spin :spinning="loading">
        <div v-if="recallResult">
          <!-- 统计信息 -->
          <div class="stats-row mb-4 flex gap-4">
            <Card size="small" class="flex-1">
              <Statistic
                title="召回实体数"
                :value="recallResult.entityCount"
                :valueStyle="{ color: '#1890ff' }"
              />
            </Card>
            <Card size="small" class="flex-1">
              <Statistic
                title="相关三元组数"
                :value="recallResult.tripleCount"
                :valueStyle="{ color: '#52c41a' }"
              />
            </Card>
            <Card size="small" class="flex-1">
              <div class="text-gray-500">查询关键词</div>
              <div class="mt-1">
                <Tag
                  v-for="kw in recallResult.keywords"
                  :key="kw"
                  color="blue"
                >
                  {{ kw }}
                </Tag>
              </div>
            </Card>
          </div>

          <!-- 召回实体表格 -->
          <Card title="召回实体列表" size="small" class="mb-4">
            <Table
              :columns="entityColumns"
              :dataSource="recallResult.entities"
              :pagination="{ pageSize: 10 }"
              size="small"
              rowKey="id"
              :locale="{ emptyText: '未召回任何实体' }"
            />
          </Card>

          <!-- 相关三元组 -->
          <Card title="相关三元组" size="small">
            <List
              v-if="recallResult.triples && recallResult.triples.length > 0"
              :dataSource="recallResult.triples"
              size="small"
              :pagination="{ pageSize: 10 }"
            >
              <template #renderItem="{ item }">
                <List.Item>
                  <code class="triple-code">{{ item }}</code>
                </List.Item>
              </template>
            </List>
            <Empty v-else description="未找到相关三元组" />
          </Card>
        </div>

        <!-- 空状态 -->
        <Empty
          v-else-if="!loading"
          description="输入关键词并点击「开始召回测试」查看结果"
          class="py-12"
        />
      </Spin>
    </div>
  </Modal>
</template>

<style lang="less" scoped>
.recall-wrapper {
  min-height: 300px;
}

.keyword-list {
  max-height: 200px;
  overflow-y: auto;
}

.stats-row {
  :deep(.ant-card-body) {
    padding: 12px 16px;
  }
}

.triple-code {
  display: block;
  padding: 4px 8px;
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
  background: #f5f5f5;
  border-radius: 4px;
}
</style>
