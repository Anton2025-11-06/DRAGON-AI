<script lang="ts" setup>
/**
 * 切片管理抽屉：分页查看某文档切片，编辑正文 / 切换可用性 / 删除切片（需求 7）。
 *
 * 改正文后端会顺带重算该片向量（SPEC：避免「显示新内容、命中旧语义」），
 * 所以保存成功后本地把 vectorized 置回、提示重算，而不是假装一切照旧。
 *
 * 删除切片是三条账一起删（MySQL 元数据 + ES 向量 + Neo4j 图谱），后端删完会回
 * 每边的条数；某一边没删干净时后端只提示不回滚，所以这里的成功提示要把 message 带出来。
 */
import { computed, reactive, ref, watch } from 'vue';

import {
  Button,
  Drawer,
  Input,
  message,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Textarea,
} from 'ant-design-vue';

import { canAction } from '#/api/acl';

import {
  CHUNK_TYPE_LABELS,
  DeleteChunk,
  PageChunks,
  UpdateChunk,
} from './api';
import type { ChunkResp, DocumentResp } from './api';

const props = defineProps<{ doc: null | DocumentResp; open: boolean }>();
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>();

const loading = ref(false);
const chunks = ref<ChunkResp[]>([]);
const pagination = reactive({ current: 1, pageSize: 20, total: 0 });
const keyword = ref('');

const editOpen = ref(false);
const editTarget = ref<null | ChunkResp>(null);
const editContent = ref('');

// 编辑正文、可用性开关、删除切片后端卡的都是所属文档的 chunk 动作码（切片管理这一位），
// 不是 edit；判错了会出现「按钮亮着、点了 403」
const canChunkDoc = computed(() => canAction(props.doc, 'chunk'));

const columns = [
  { dataIndex: 'chunkIndex', key: 'chunkIndex', title: '#', width: 60 },
  { dataIndex: 'chunkType', key: 'chunkType', title: '模态', width: 80 },
  { dataIndex: 'content', key: 'content', title: '正文', ellipsis: true },
  { dataIndex: 'available', key: 'available', title: '可用', width: 80 },
  { dataIndex: 'action', key: 'action', title: '操作', width: 150 },
];

async function load() {
  if (!props.doc) return;
  loading.value = true;
  try {
    const res = await PageChunks(props.doc.id, {
      current: pagination.current,
      size: pagination.pageSize,
      keyword: keyword.value || undefined,
    });
    chunks.value = res.records || [];
    pagination.total = res.total || 0;
  } catch (e: any) {
    message.error(e?.message || '加载切片失败');
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      pagination.current = 1;
      keyword.value = '';
      load();
    }
  },
);

function onPageChange(pag: any) {
  pagination.current = pag.current;
  load();
}

function openEdit(c: ChunkResp) {
  editTarget.value = c;
  editContent.value = c.content;
  editOpen.value = true;
}

async function saveContent() {
  if (!editTarget.value) return;
  await UpdateChunk(editTarget.value.id, { content: editContent.value });
  message.success('已保存，正在重算该切片向量');
  editOpen.value = false;
  load();
}

async function toggleAvailable(c: ChunkResp, checked: boolean) {
  await UpdateChunk(c.id, { available: checked ? 1 : 0 });
  c.available = checked;
  message.success(checked ? '已启用' : '已停用（检索将排除）');
}

/** 删除前二次确认：向量与图谱数据删下去就拉不回来，只能重跑构建向量/构建图谱 */
function askDelete(c: ChunkResp) {
  Modal.confirm({
    content:
      `删除第 ${c.chunkIndex} 个切片？会一起清掉它的元数据、向量数据和图谱数据，` +
      `删掉只能重跑构建向量、构建图谱才能再补回来。`,
    okButtonProps: { danger: true },
    okText: '确定删除',
    title: '确认删除切片',
    async onOk() {
      try {
        const res: any = await DeleteChunk(c.id);
        const parts = [
          `向量 ${res?.esDeleted ?? 0} 条`,
          `图谱实体 ${res?.graphDeletedNodes ?? 0} 个、关系 ${res?.graphDeletedRels ?? 0} 条`,
        ];
        message.success(`已删除切片，同时清掉${parts.join('、')}`);
        if (res?.message) message.warning(res.message);
        // 后端已把文档的 chunk_count 回写，这里重拉一页，别让总数停在旧值上
        load();
      } catch (e: any) {
        message.error(e?.message || '删除切片失败');
      }
    },
  });
}
</script>

<template>
  <Drawer
    :open="open"
    :width="900"
    :title="`切片管理 - ${doc?.title || doc?.fileName || ''}`"
    @update:open="emit('update:open', $event)"
  >
    <Space class="mb-3">
      <Input
        v-model:value="keyword"
        placeholder="按正文关键词过滤"
        allow-clear
        style="width: 240px"
        @press-enter="() => { pagination.current = 1; load(); }"
      />
      <Button @click="() => { pagination.current = 1; load(); }">搜索</Button>
    </Space>

    <Table
      :columns="columns"
      :data-source="chunks"
      :loading="loading"
      :pagination="{
        current: pagination.current,
        pageSize: pagination.pageSize,
        total: pagination.total,
        showSizeChanger: false,
      }"
      row-key="id"
      size="small"
      @change="onPageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'chunkType'">
          <Tag>{{ CHUNK_TYPE_LABELS[record.chunkType] || record.chunkType }}</Tag>
        </template>
        <template v-else-if="column.key === 'content'">
          <span class="chunk-text">{{ record.content }}</span>
        </template>
        <template v-else-if="column.key === 'available'">
          <Switch
            :checked="record.available"
            :disabled="!canChunkDoc"
            size="small"
            @change="(v: any) => toggleAvailable(record as ChunkResp, v)"
          />
        </template>
        <template v-else-if="column.key === 'action'">
          <Space v-if="canChunkDoc" :size="8">
            <a @click="openEdit(record as ChunkResp)">编辑正文</a>
            <a class="chunk-danger" @click="askDelete(record as ChunkResp)">删除切片</a>
          </Space>
          <span v-else class="chunk-text">只读</span>
        </template>
      </template>
    </Table>

    <Modal
      v-model:open="editOpen"
      title="编辑切片正文"
      :width="720"
      @ok="saveContent"
    >
      <Textarea v-model:value="editContent" :rows="14" />
    </Modal>
  </Drawer>
</template>

<style scoped>
.chunk-text {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.chunk-danger {
  color: #ff4d4f;
}
</style>
