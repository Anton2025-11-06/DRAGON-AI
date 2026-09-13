<script lang="ts" setup>
import type { McpToolInfo } from './api';

import { onMounted, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';

interface Props {
  visible: boolean;
  mcpServer: any;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void;
}>();

const loading = ref(false);
const tools = ref<McpToolInfo[]>([]);

const columns = [
  {
    title: '工具名称',
    dataIndex: 'name',
    key: 'name',
    width: 200,
  },
  {
    title: '工具描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  {
    title: '参数',
    key: 'params',
    width: 90,
  },
];

const schemaColumns = [
  { title: '参数名', dataIndex: 'name', key: 'name', width: 180 },
  { title: '类型', dataIndex: 'type', key: 'type', width: 90 },
  { title: '必填', dataIndex: 'required', key: 'required', width: 70 },
  { title: '说明', dataIndex: 'desc', key: 'desc', ellipsis: true },
];

/** 展开行：完整 JSON Schema 展示 */
const schemaOf = (record: McpToolInfo) => {
  const schema = record?.inputSchema;
  const props = schema?.properties;
  const required = Array.isArray(schema?.required) ? schema.required : [];
  if (!props || typeof props !== 'object') return null;
  const rows = Object.entries(props).map(([name, p]: [string, any]) => ({
    name,
    type: p?.type || typeof p,
    desc: p?.description || '',
    required: required.includes(name),
  }));
  return rows;
};

const loadTools = async () => {
  if (!props.mcpServer?.id) return;

  loading.value = true;
  try {
    const result = await api.GetTools(props.mcpServer.id);
    tools.value = result || [];
  } catch (error: any) {
    message.error(`获取工具列表失败: ${error.message}`);
    tools.value = [];
  } finally {
    loading.value = false;
  }
};

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      loadTools();
    }
  },
);

onMounted(() => {
  if (props.visible) {
    loadTools();
  }
});

const handleClose = () => {
  emit('update:visible', false);
};
</script>

<template>
  <a-modal
    :open="visible"
    :title="`MCP工具列表 - ${mcpServer?.name || ''}`"
    width="800px"
    :footer="null"
    @cancel="handleClose"
  >
    <a-alert
      v-if="!loading && tools.length === 0"
      message="暂无可用工具"
      type="info"
      show-icon
      style="margin-bottom: 16px"
    />

    <a-table
      :columns="columns"
      :data-source="tools"
      :loading="loading"
      :pagination="false"
      row-key="name"
      size="middle"
      :scroll="{ y: 400 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'params'">
          <a-tag v-if="schemaOf(record)" color="blue">
            {{ (schemaOf(record) || []).length }}
          </a-tag>
          <span v-else class="no-schema">—</span>
        </template>
      </template>
      <template #expandedRowRender="{ record }">
        <div v-if="schemaOf(record)" class="schema-wrap">
          <a-table
            :data-source="schemaOf(record)"
            :columns="schemaColumns"
            :pagination="false"
            size="small"
            row-key="name"
          />
        </div>
        <pre v-else class="schema-raw">{{ JSON.stringify(record.inputSchema || {}, null, 2) }}</pre>
      </template>
      <template #emptyText>
        <a-empty description="暂无工具数据" />
      </template>
    </a-table>

    <template #footer>
      <a-button type="primary" @click="handleClose">关闭</a-button>
    </template>
  </a-modal>
</template>

<style scoped>
.no-schema {
  color: #bfbfbf;
}

.schema-wrap {
  padding: 4px 8px;
}

.schema-raw {
  margin: 0;
  padding: 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  background: #fafafa;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
