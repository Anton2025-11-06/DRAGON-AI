<script lang="ts" setup>
import type { McpToolInfo } from './api';

import { onMounted, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';
import { paramsSummary, schemaParams } from './schema';
import ToolTestModal from './ToolTestModal.vue';

interface Props {
  mcpServer: any;
  visible: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void;
}>();

const loading = ref(false);
const tools = ref<McpToolInfo[]>([]);

const testOpen = ref(false);
const testTool = ref<McpToolInfo | null>(null);

const columns = [
  { dataIndex: 'name', key: 'name', title: '工具名称', width: 200 },
  {
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
    title: '工具描述',
  },
  { key: 'params', title: '参数', width: 160 },
  { fixed: 'right', key: 'action', title: '操作', width: 80 },
];

const schemaColumns = [
  { dataIndex: 'name', key: 'name', title: '参数名', width: 160 },
  { dataIndex: 'type', key: 'type', title: '类型', width: 90 },
  { key: 'required', title: '必填', width: 70 },
  { dataIndex: 'desc', key: 'desc', ellipsis: true, title: '说明' },
];

/** 展开行与参数列共用一份解析结果，避免同一个 schema 解析两遍口径不一 */
const schemaOf = (record: McpToolInfo) => schemaParams(record?.inputSchema);

const loadTools = async () => {
  if (!props.mcpServer?.id) return;

  loading.value = true;
  try {
    const result = await api.GetTools(props.mcpServer.id);
    tools.value = result || [];
  } catch (error: any) {
    // 连接不通时后端现在直接报错，不再静默返回空列表，这里要把原因摊开
    message.error(`获取工具列表失败: ${error?.message || '未知错误'}`);
    tools.value = [];
  } finally {
    loading.value = false;
  }
};

function openTest(tool: McpToolInfo) {
  testTool.value = tool;
  testOpen.value = true;
}

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
    :footer="null"
    :open="visible"
    :title="`MCP工具列表 - ${mcpServer?.name || ''}`"
    width="900px"
    @cancel="handleClose"
  >
    <a-alert
      v-if="!loading && tools.length === 0"
      message="暂无可用工具"
      show-icon
      style="margin-bottom: 16px"
      type="info"
    />

    <a-table
      :columns="columns"
      :data-source="tools"
      :loading="loading"
      :pagination="false"
      :scroll="{ x: 860, y: 400 }"
      row-key="name"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'params'">
          <span v-if="schemaOf(record)" class="params-text">
            {{ paramsSummary(schemaOf(record)) }}
          </span>
          <span v-else class="no-schema">无参数</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <a @click="openTest(record)">测试</a>
        </template>
      </template>
      <template #expandedRowRender="{ record }">
        <div v-if="schemaOf(record)" class="schema-wrap">
          <a-table
            :columns="schemaColumns"
            :data-source="schemaOf(record)"
            :pagination="false"
            row-key="name"
            size="small"
          >
            <template #bodyCell="{ column: schemaColumn, record: param }">
              <template v-if="schemaColumn.key === 'required'">
                <a-tag :color="param.required ? 'orange' : 'default'">
                  {{ param.required ? '必填' : '选填' }}
                </a-tag>
              </template>
            </template>
          </a-table>
        </div>
        <pre v-else class="schema-raw">{{
          JSON.stringify(record.inputSchema || {}, null, 2)
        }}</pre>
      </template>
      <template #emptyText>
        <a-empty description="暂无工具数据" />
      </template>
    </a-table>

    <template #footer>
      <a-button type="primary" @click="handleClose">关闭</a-button>
    </template>
  </a-modal>

  <ToolTestModal
    v-model:open="testOpen"
    :server-id="mcpServer?.id"
    :server-name="mcpServer?.name"
    :tool="testTool"
  />
</template>

<style scoped>
.params-text {
  font-size: 12px;
  color: #595959;
}

.no-schema {
  color: #bfbfbf;
}

.schema-wrap {
  padding: 4px 8px;
}

.schema-raw {
  max-height: 260px;
  padding: 10px;
  margin: 0;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  color: #fff;
  word-break: break-all;
  white-space: pre-wrap;

  /* 钉住底色：全局 pre 样式一旦把它盖成浅底，白字就直接隐形 */
  background: #1e1e1e !important;
  border-radius: 6px;
}
</style>
