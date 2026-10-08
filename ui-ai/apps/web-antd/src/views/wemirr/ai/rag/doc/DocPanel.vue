<script lang="ts" setup>
/**
 * 文档面板：三类知识库共用一张表格（SPEC §11.1-4）
 *
 * 图片墙与音视频播放器卡片已合回表格：列表页风格以文档库为基准统一，列与行操作按 kbType 算：
 * - doc    多出「切片」「图谱进度」两列与「查看解析后文档」「切片管理」「构建图谱」三个入口；
 * - 媒体型 多出「创建人」一列，行操作只留 预览原文件 / 重试 / 删除（图谱与切片概念不适用于它们，
 *   非 doc 型的图谱支持位恒为 False，放个按钮上去就是个点了必失败的坑）；
 * - 分页控件对三类都给（媒体型以前是裸 v-for，pageSize 传给了后端但页面上没有翻页位，
 *   第 N 条之后的资源永远看不到）；
 * - 多媒体不再自动加载：列表不再下发签名地址，点预览才当场签一个（也是为了让
 *   预览这一步真的过 preview 鉴权）。
 *
 * 进度靠 /documents/progress/batch：每次加载先整屏拉一次（分段进度条即使没在跑也要画得出来），
 * 只有还有文档在跑时才起 2.5s 定时器，有文档转入终态就刷新列表。
 * 媒体链路（download/preprocess/embedding/index）的分段进度后端一直在写，
 * 以前只是没在页面上画出来，现在三条链路共用同一个分段进度条。
 *
 * 三个口径变更：
 * - 行内操作一律图标化（只把名字放在悬停 tooltip 里）：doc 型定死四项，每一项对应
 *   一个自己的 ACL 动作码；工具栏只余批量删除位（批量重试与取消位都已撤）；
 * - 「重新分块」改名「构建向量」、「建图谱」改名「构建图谱」（需求 10）：两者都必须先勾选文档，
 *   且点击后先弹二次确认（会删旧数据，不可逆）；
 * - 「摄取」这个词已从全部文案里消失（需求 14），中文一律叫「解析」。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';

import {
  ApartmentOutlined,
  DeleteOutlined,
  EyeOutlined,
  FileSearchOutlined,
  ProfileOutlined,
  RedoOutlined,
  ReloadOutlined,
  ScissorOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  message,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { canAction } from '#/api/acl';

import {
  BuildGraph,
  BuildVectors,
  DeleteDocuments,
  DOC_STATUS_COLORS,
  DOC_STATUS_LABELS,
  DocPageList,
  GetDocsProgressBatch,
  GetDocOriginal,
  KB_TYPE_AUDIO_VIDEO,
  KB_TYPE_DOC,
  KB_TYPE_IMAGE,
  KG_STATE_COLORS,
  KG_STATE_LABELS,
  RenameDoc,
  RetryDocs,
} from './api';
import type {
  DocProgressResp,
  DocumentResp,
  KnowledgeBaseResp,
} from './api';
import ChunkDrawer from './ChunkDrawer.vue';
import DocContentModal from './DocContentModal.vue';
import UploadModal from './UploadModal.vue';

const props = defineProps<{ kb: null | KnowledgeBaseResp }>();

const loading = ref(false);
const docs = ref<DocumentResp[]>([]);
const selectedIds = ref<number[]>([]);
const progressMap = ref<Record<number, DocProgressResp>>({});
// 默认一页 10 条：与 showSizeChanger 的候选档对齐（12 不在候选里，下拉会出现一个孤儿档位）
const pagination = ref({ current: 1, pageSize: 10, total: 0 });

const uploadOpen = ref(false);
const contentOpen = ref(false);
const contentDocId = ref<null | number>(null);
const contentTitle = ref('');
const chunkOpen = ref(false);
const chunkDoc = ref<null | DocumentResp>(null);
const renameOpen = ref(false);
const renameValue = ref('');
const renameTarget = ref<null | DocumentResp>(null);
// 构建向量 / 构建图谱的二次确认弹框（需求 10）：点按钮只开弹框，点确定才真发请求
const vectorOpen = ref(false);
const vectorReparse = ref(false);
const graphOpen = ref(false);
// 断点续抽默认勾选：抽取是按批走大模型的，上一轮失败时前面那几批已经烧过一遍调用，
//   再点一次构建不该从头重烧（取消勾选 = 换了抽取模型/提示词后的全量重抽）
const graphResume = ref(true);

const isDoc = computed(() => props.kb?.kbType === KB_TYPE_DOC);
const isImage = computed(() => props.kb?.kbType === KB_TYPE_IMAGE);
const isAv = computed(() => props.kb?.kbType === KB_TYPE_AUDIO_VIDEO);
/** 图片型与音视频型共用媒体列集（没勾选知识库时两个都是 false，表格不渲染） */
const isMedia = computed(() => isImage.value || isAv.value);

const RUNNING = ['ANALYZING', 'PENDING', 'PROCESSING', 'PARSING'];

/**
 * 按钮只认后端下发的 actions（文档列表接口已按 ACL 逐行求值），判定规则不在前端复制另一份。
 * 动作码与后端 doc_service 一一对应：预览 preview / 切片 chunk / 构建向量 reparse /
 * 构建图谱 graph / 改标题 edit / 删除 delete。
 */
const canRenameRow = (d: DocumentResp) => canAction(d, 'edit');
const canPreviewRow = (d: DocumentResp) => canAction(d, 'preview');
const canChunkRow = (d: DocumentResp) => canAction(d, 'chunk');
const canDeleteRow = (d: DocumentResp) => canAction(d, 'delete');
// 重跑解析链路的动作码，现在只有媒体行上的「重试」图标用它：doc 型的行操作定死为四项
// （需求 9），工具栏的批量重试位也一并撤了 —— doc 型要重跑走「构建向量」，它底层是同一个投递
const canRetryRow = (d: DocumentResp) => canAction(d, 'reparse');
// 查看解析后文档不新开动作码：它就是看这篇文档的内容，与详情同源（后端给 view）
const canViewRow = (d: DocumentResp) => canAction(d, 'view');

const canUploadToKb = computed(() => canAction(props.kb, 'upload'));

/** 勾选态如今只决定工具栏要不要出「删除」位（批量重试/取消位已撤，构建向量与图谱直接读 selectedIds） */
const hasSelection = computed(() => selectedIds.value.length > 0);

function formatSize(bytes: number) {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${Number.parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
}
function formatDuration(sec: number) {
  if (!sec) return '-';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function load(keepSelection = false) {
  if (!props.kb) {
    docs.value = [];
    return;
  }
  loading.value = true;
  try {
    const res = await DocPageList(props.kb.id, {
      current: pagination.value.current,
      size: pagination.value.pageSize,
    });
    docs.value = res.records || [];
    pagination.value.total = res.total || 0;
    if (!keepSelection) selectedIds.value = [];
    // 分段进度条不只在「跑动中」才需要：已成/已败的文档也要把「哪几个阶段、绿了几段」画出来
    await refreshProgress();
    ensurePolling();
  } catch (e: any) {
    message.error(e?.message || '加载文档失败');
  } finally {
    loading.value = false;
  }
}

// ---------- 进度轮询 ----------
let timer: ReturnType<typeof setInterval> | null = null;

/** 向量化与图谱是两个独立状态机，任一边在跑就得继续轮询 */
function isBusy(d: DocumentResp) {
  return RUNNING.includes(d.status || '') || Number(d.graphState) === 1;
}

function ensurePolling() {
  if (!docs.value.some(isBusy)) {
    stopPolling();
    return;
  }
  if (!timer) timer = setInterval(refreshProgress, 2500);
}
function stopPolling() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

/** 整屏拉一次进度（不分在不在跑），并按结果判断要不要刷新列表 */
async function refreshProgress() {
  if (!props.kb || docs.value.length === 0) return stopPolling();
  const ids = docs.value.map((d) => d.id);
  try {
    const list = await GetDocsProgressBatch(ids);
    let finished = false;
    const map = { ...progressMap.value };
    list.forEach((p) => {
      map[p.docId] = p;
      const d = docs.value.find((x) => x.id === p.docId);
      if (!d) return;
      if (p.status && RUNNING.includes(d.status || '') && !RUNNING.includes(p.status)) {
        finished = true;
      }
      // 图谱从「构建中」落到终态同样要重拉列表（graphState 是权威值，不在进度里）
      if (
        Number(d.graphState) === 1 &&
        p.graphState !== undefined &&
        Number(p.graphState) !== 1
      ) {
        finished = true;
      }
    });
    progressMap.value = map;
    if (finished) await load(true);
  } catch {
    /* 轮询失败静默，下一轮再试 */
  }
}

watch(
  () => props.kb?.id,
  () => {
    stopPolling();
    progressMap.value = {};
    pagination.value.current = 1;
    load();
  },
  { immediate: true },
);
onBeforeUnmount(stopPolling);

// ---------- 动作 ----------
function onTableChange(pag: any) {
  pagination.value.current = pag.current;
  pagination.value.pageSize = pag.pageSize;
  load();
}

function openUpload() {
  uploadOpen.value = true;
}

function openContent(d: DocumentResp) {
  contentDocId.value = d.id;
  contentTitle.value = d.title || d.fileName || '';
  contentOpen.value = true;
}
/** 预览原文件：拿后端签好的只读地址直接开新窗口（pdf/图/音视频都走这一条） */
async function openOriginal(d: DocumentResp) {
  try {
    const res = await GetDocOriginal(d.id);
    if (!res?.url) return message.warning('这个文档没有可访问的原件地址');
    window.open(res.url, '_blank', 'noopener');
  } catch (e: any) {
    message.error(e?.message || '获取原文件失败');
  }
}
function openChunks(d: DocumentResp) {
  chunkDoc.value = d;
  chunkOpen.value = true;
}

function openRename(d: DocumentResp) {
  renameTarget.value = d;
  renameValue.value = d.title || '';
  renameOpen.value = true;
}
async function doRename() {
  if (!renameTarget.value || !renameValue.value.trim()) return;
  await RenameDoc(renameTarget.value.id, renameValue.value.trim());
  message.success('已修改');
  renameOpen.value = false;
  load(true);
}

async function doRetry(d: DocumentResp) {
  await RetryDocs({ docIds: [d.id] });
  message.success('已重新投递');
  load(true);
}
async function doDeleteOne(d: DocumentResp) {
  if (!props.kb) return;
  Modal.confirm({
    content: `删除文档「${d.title || d.fileName}」？底层切片与实体将异步清理。`,
    title: '确认删除',
    async onOk() {
      await DeleteDocuments(props.kb!.id, [d.id]);
      message.success('已删除');
      load(true);
    },
  });
}
async function doBatchDelete() {
  if (!props.kb || selectedIds.value.length === 0) return;
  Modal.confirm({
    content: `删除选中的 ${selectedIds.value.length} 个文档？`,
    title: '批量删除',
    async onOk() {
      await DeleteDocuments(props.kb!.id, selectedIds.value);
      message.success('已提交删除');
      load();
    },
  });
}
async function doBuildVectors() {
  if (!props.kb) return;
  // 需求 10：这个按钮在上传右边，必须勾选文档。不给「没勾 = 整库重跑」的兜底，
  // 误点一次的代价是整库重做向量化，所以宁可弹一句提示
  if (selectedIds.value.length === 0) return message.warning('请选择目标文档');
  vectorReparse.value = false;
  vectorOpen.value = true;
}

/** 二次确认才真发请求：先删旧向量再重跑，删下去就拉不回来 */
async function confirmBuildVectors() {
  if (!props.kb) return;
  const res = await BuildVectors(props.kb!.id, {
    docIds: selectedIds.value,
    reparse: vectorReparse.value,
  });
  const purged = (res as any)?.purged ?? 0;
  message.success(
    `已投递 ${res.accepted} 个构建向量任务` + (purged ? `，清理旧向量 ${purged} 条` : ''),
  );
  vectorOpen.value = false;
  load(true);
}

async function doBuildGraph() {
  if (!props.kb) return;
  if (selectedIds.value.length === 0) return message.warning('请选择目标文档');
  graphResume.value = true;
  graphOpen.value = true;
}

/**
 * 构建图谱恒带 force：这个按钮的语义就是「先清掉这篇文档的旧图谱数据再重抽」；
 * resume 单独给一个勾选项：清旧图与重烧模型调用是两件事，旧图要清但已抽成功的批可以留。
 */
async function confirmBuildGraph() {
  if (!props.kb) return;
  const res = await BuildGraph(props.kb.id, {
    docIds: selectedIds.value,
    force: true,
    resume: graphResume.value,
  });
  message.success(
    `已投递 ${res.accepted} 个构建图谱任务` +
      (res.skipped ? `，跳过 ${res.skipped} 个` : ''),
  );
  graphOpen.value = false;
  load(true);
}

// ---------- 分段进度条（需求 11）----------
type StageFlag = { code: string; done: boolean; label: string };

/**
 * 阶段数组由后端下发（stage_flags）：每段 {code,label,done}，段数 = 阶段数。
 * 进度接口没回或回得早于阶段表时拿不到数组，那种情况只保留状态 Tag，
 * 不拿前端自己拼的假阶段去误导人「已经跑到第几步了」。
 */
function vectorStages(d: DocumentResp): StageFlag[] {
  return (progressMap.value[d.id]?.stages || []) as StageFlag[];
}
function graphStages(d: DocumentResp): StageFlag[] {
  return (progressMap.value[d.id]?.graphStages || []) as StageFlag[];
}

function vectorText(d: DocumentResp) {
  const p = progressMap.value[d.id];
  if (d.status === 'PROCESSED') return '已完成';
  if (d.status === 'FAILED') return d.errorMsg ? `失败：${String(d.errorMsg).slice(0, 30)}` : '失败';
  if (!p) return d.statusLabel || DOC_STATUS_LABELS[d.status || ''] || '';
  const label = p.stageLabel || p.stage || '';
  return `${label} ${p.done ?? 0}/${p.total ?? 0} (${p.percent ?? 0}%)`;
}

function graphText(d: DocumentResp) {
  const p = progressMap.value[d.id];
  const state = Number(d.graphState);
  if (state === 1 && p) {
    return `${p.graphStageLabel || p.graphStage || ''} ${p.graphDone ?? 0}/${p.graphTotal ?? 0} (${p.graphPercent ?? 0}%)`;
  }
  return d.graphStateLabel || KG_STATE_LABELS[state] || '未构建';
}

/** 列表显示与悬停全称同一个口径：title 优先，没改过标题就回落到原始文件名 */
function docName(d: DocumentResp) {
  return d.title || d.fileName || '';
}

/**
 * 列按库类型算：媒体型不给切片列与图谱列（需求：图片/音视频列表页删掉图谱相关元素、切片字段不需要）。
 * 时长并入「大小」列而不是单开一列：只有音视频型有值，单开一列在文档库与图片库里是一片空列。
 */
const tableColumns = computed<any[]>(() => {
  const cols: any[] = [
    {
      dataIndex: 'title',
      key: 'title',
      title: isDoc.value ? '文档' : '资源名称',
      // 名称列定宽：不给宽度时它吃掉 scroll.x 的剩余空间，长文件名直接折行，
      // 同一列的行高被撑成一忽儿两三种高度；定宽后配合 .doc-name 截断成单行
      width: 240,
    },
    {
      dataIndex: 'fileSize',
      key: 'fileSize',
      title: '大小',
      width: isAv.value ? 170 : 110,
    },
    {
      dataIndex: 'status',
      key: 'status',
      title: isDoc.value ? '向量化进度' : '解析状态',
      width: 240,
    },
  ];
  if (isDoc.value) {
    cols.push(
      {
        dataIndex: 'chunkCount',
        key: 'chunkCount',
        title: '切片',
        width: 80,
      },
      {
        dataIndex: 'graphState',
        key: 'graphState',
        title: '图谱进度',
        width: 220,
      },
    );
  } else {
    cols.push({
      dataIndex: 'creatorName',
      key: 'creatorName',
      title: '创建人',
      width: 110,
    });
  }
  // 行操作已是纯图标：doc 型四个、媒体型最多三个，格子按图标位数给宽
  cols.push(
    {
      dataIndex: 'createTime',
      key: 'createTime',
      title: '创建时间',
      width: 160,
    },
    {
      dataIndex: 'action',
      key: 'action',
      title: '操作',
      width: isDoc.value ? 150 : 120,
      fixed: 'right',
    },
  );
  return cols;
});

/** 三类库统一给分页（媒体型以前没有这一位，第 11 条就再也翻不到了） */
const tablePagination = computed(() => ({
  current: pagination.value.current,
  pageSize: pagination.value.pageSize,
  total: pagination.value.total,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (total: number) => `共 ${total} 条`,
}));

const scrollX = computed(() => (isDoc.value ? 1240 : 1020));

const rowSelection = computed<any>(() => ({
  selectedRowKeys: selectedIds.value,
  onChange: (keys: any[]) => (selectedIds.value = keys.map(Number)),
}));
</script>

<template>
  <Card class="doc-card" :bordered="false">
    <template #title>
      <div v-if="kb" class="flex items-center">
        <span class="font-medium">{{ kb.name }}</span>
        <Tag class="ml-2" color="blue">{{ kb.kbTypeLabel }}</Tag>
      </div>
      <div v-else>请选择知识库</div>
    </template>
    <template #extra>
      <Space v-if="kb">
        <Button
          v-if="canUploadToKb"
          type="primary"
          size="small"
          @click="openUpload"
        >
          <template #icon><UploadOutlined /></template>
          上传
        </Button>
        <!-- 批量位对三类库都给（媒体库一样能重跑向量）；只有图谱是 doc 型专属 -->
        <Tooltip title="先删除这些文档已有的向量数据，再按当前分块配置重新分块并向量化（旧名「重新分块」）">
          <Button size="small" @click="doBuildVectors">
            <template #icon><ScissorOutlined /></template>
            构建向量
          </Button>
        </Tooltip>
        <Tooltip
          v-if="isDoc"
          title="先删除这些文档已有的图谱数据，再重新抽取实体与关系（需库上开了图谱并配了抽取模型）"
        >
          <Button size="small" @click="doBuildGraph">
            <template #icon><ApartmentOutlined /></template>
            构建图谱
          </Button>
        </Tooltip>
        <Button v-if="hasSelection" size="small" danger @click="doBatchDelete">
          删除({{ selectedIds.length }})
        </Button>
        <Button size="small" @click="load()">
          <template #icon><ReloadOutlined /></template>
        </Button>
      </Space>
    </template>

    <Spin :spinning="loading">
      <!-- 三类知识库统一一张表格：列与行操作按 kbType 算（tableColumns），媒体型不再单开形态 -->
      <Table
        v-if="isDoc || isMedia"
        :columns="tableColumns"
        :data-source="docs"
        :pagination="tablePagination"
        :row-selection="rowSelection"
        row-key="id"
        size="small"
        :scroll="{ x: scrollX }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'title'">
            <!-- 名称截断成一行，完整名称交给悬停 Tooltip -->
            <div class="doc-name-cell">
              <Tooltip :title="docName(record as DocumentResp)">
                <a
                  v-if="canRenameRow(record as DocumentResp)"
                  class="doc-name"
                  @click="openRename(record as DocumentResp)"
                >
                  {{ docName(record as DocumentResp) }}
                </a>
                <span v-else class="doc-name">{{ docName(record as DocumentResp) }}</span>
              </Tooltip>
            </div>
          </template>
          <template v-else-if="column.key === 'fileSize'">
            {{ formatSize(record.fileSize) }}
            <span v-if="isAv" class="progress-text">
              · 时长 {{ formatDuration(record.mediaDuration) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <div class="progress-cell">
              <Tag :color="DOC_STATUS_COLORS[record.status] || 'default'">
                {{ record.statusLabel || DOC_STATUS_LABELS[record.status] || record.status }}
              </Tag>
              <span class="progress-text">{{ vectorText(record as DocumentResp) }}</span>
            </div>
            <!-- 按阶段数分段：已完成的段绿、未完成的段蓝，一眼看出卡在第三步还是第五步 -->
            <div class="stage-bar">
              <Tooltip
                v-for="s in vectorStages(record as DocumentResp)"
                :key="s.code"
                :title="`${s.label}·${s.done ? '已完成' : '未完成'}`"
              >
                <div class="stage-seg" :class="{ done: s.done }"></div>
              </Tooltip>
            </div>
          </template>
          <template v-else-if="column.key === 'graphState'">
            <div class="progress-cell">
              <Tag :color="KG_STATE_COLORS[record.graphState] || 'default'">
                {{ record.graphStateLabel || KG_STATE_LABELS[record.graphState] }}
              </Tag>
              <span class="progress-text">{{ graphText(record as DocumentResp) }}</span>
            </div>
            <div class="stage-bar">
              <Tooltip
                v-for="s in graphStages(record as DocumentResp)"
                :key="s.code"
                :title="`${s.label}·${s.done ? '已完成' : '未完成'}`"
              >
                <div class="stage-seg" :class="{ done: s.done }"></div>
              </Tooltip>
            </div>
          </template>
          <template v-else-if="column.key === 'creatorName'">
            {{ record.creatorName || '-' }}
          </template>
          <template v-else-if="column.key === 'action'">
            <!-- 行操作全部图标化（文案太长把表格挤变形），名字只放在悬停 tooltip 里；
                 每个位仍只认后端下发的一个动作码，判定规则不在前端复制第二份 -->
            <Space :size="0">
              <Tooltip
                v-if="canPreviewRow(record as DocumentResp)"
                title="预览原文件"
              >
                <Button
                  type="text"
                  size="small"
                  @click="openOriginal(record as DocumentResp)"
                >
                  <template #icon><FileSearchOutlined /></template>
                </Button>
              </Tooltip>
              <!-- 媒体型没有「解析后内容」与「切片」这两个概念，两个位只对 doc 型出现 -->
              <Tooltip
                v-if="isDoc && canViewRow(record as DocumentResp)"
                title="查看解析后内容"
              >
                <Button
                  type="text"
                  size="small"
                  @click="openContent(record as DocumentResp)"
                >
                  <template #icon><EyeOutlined /></template>
                </Button>
              </Tooltip>
              <Tooltip
                v-if="isDoc && canChunkRow(record as DocumentResp)"
                title="切片管理"
              >
                <Button
                  type="text"
                  size="small"
                  @click="openChunks(record as DocumentResp)"
                >
                  <template #icon><ProfileOutlined /></template>
                </Button>
              </Tooltip>
              <!-- 媒体行的重试就地放：批量重试位撤掉后，这是页面上唯一直接重跑的地方，
                   单张图失败时不必先摸鼠标去勾它；doc 型保持需求 9 定死的四项，重跑走「构建向量」 -->
              <Tooltip
                v-if="
                  !isDoc &&
                  record.status === 'FAILED' &&
                  canRetryRow(record as DocumentResp)
                "
                title="重试"
              >
                <Button
                  type="text"
                  size="small"
                  @click="doRetry(record as DocumentResp)"
                >
                  <template #icon><RedoOutlined /></template>
                </Button>
              </Tooltip>
              <Tooltip v-if="canDeleteRow(record as DocumentResp)" title="删除">
                <Button
                  danger
                  type="text"
                  size="small"
                  @click="doDeleteOne(record as DocumentResp)"
                >
                  <template #icon><DeleteOutlined /></template>
                </Button>
              </Tooltip>
            </Space>
          </template>
        </template>
      </Table>

      <Empty v-else description="请从左侧选择知识库" />
    </Spin>

    <UploadModal
      v-model:open="uploadOpen"
      :kb-id="kb?.id ?? null"
      :kb-type="kb?.kbType || 'doc'"
      :kb-name="kb?.name"
      @uploaded="load()"
    />
    <DocContentModal
      v-model:open="contentOpen"
      :doc-id="contentDocId"
      :title="contentTitle"
    />
    <ChunkDrawer v-model:open="chunkOpen" :doc="chunkDoc" />

    <Modal
      v-model:open="renameOpen"
      title="修改文档标题"
      @ok="doRename"
    >
      <Input v-model:value="renameValue" placeholder="请输入新标题" />
    </Modal>

    <!-- 需求 10：两个构建动作都是「先删旧的再重建」，不可逆，所以必须二次确认；
         点「取消」什么都不发生，已勾选的行保持勾选 -->
    <Modal
      v-model:open="vectorOpen"
      title="构建向量"
      ok-text="确定构建"
      cancel-text="取消"
      :ok-button-props="{ danger: true }"
      @ok="confirmBuildVectors"
    >
      <p>
        将对已勾选的 {{ selectedIds.length }} 个文档：
        <strong>先删除它们已有的向量数据</strong>，再按当前分块配置重新分块与向量化。
      </p>
      <p class="modal-warn">旧向量删掉后不可恢复，重跑期间这些文档可能检索不到。</p>
      <Checkbox v-model:checked="vectorReparse">
        连文档解析一起重跑（默认复用上一次解析产物，只重做分块与向量）
      </Checkbox>
    </Modal>

    <Modal
      v-model:open="graphOpen"
      title="构建图谱"
      ok-text="确定构建"
      cancel-text="取消"
      :ok-button-props="{ danger: true }"
      @ok="confirmBuildGraph"
    >
      <p>
        将对已勾选的 {{ selectedIds.length }} 个文档：
        <strong>先删除它们已有的图谱数据（实体与关系）</strong>，再重新抽取并写入图谱。
      </p>
      <p class="modal-warn">未开图谱、没配抽取模型或还没解析完成的文档会被后端跳过，回执里会报数量。</p>
      <Checkbox v-model:checked="graphResume">
        断点续抽（上一轮已抽成功的分块直接复用，只补失败那一批往后的）
      </Checkbox>
    </Modal>
  </Card>
</template>

<style lang="less" scoped>
.doc-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;

  /* 工具栏（标题 + 上传/构建那排按钮）不动，分页后的文档在这块里上下滚；
     min-height: 0 同左栏：没这一条 flex 项不收缩，整张表会把卡片顶高到看不全 */
  :deep(.ant-card-body) {
    flex: 1;
    padding: 12px;
    min-height: 0;
    overflow: auto;
  }
}

.modal-warn {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text-color-secondary, #d46b08);
}

/* 名称格：名字弹性截断，尾部标签固定不被挤掉（min-width:0 让 flex 项允许收缩到
   小于内容宽度，不然省略号不会生效） */
.doc-name-cell {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
}

.doc-name {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 分段进度条（需求 11）：宽度按阶段数均分，已完成绿、未完成蓝 */
.progress-cell {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 4px;
}

.progress-text {
  font-size: 12px;
  color: var(--text-color-secondary, #888);
}

.stage-bar {
  display: flex;
  gap: 2px;
  align-items: center;
}

.stage-seg {
  flex: 1;
  height: 6px;
  background: #1890ff;
  border-radius: 2px;
}

.stage-seg.done {
  background: #52c41a;
}
</style>
