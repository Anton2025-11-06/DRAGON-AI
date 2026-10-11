<script lang="ts" setup name="RagKnowledgeEval">
/**
 * 知识评测页（RAGAS）：指定知识库 + 问答对数据集，跑五指标 + 三段耗时评估召回与问答质量。
 *
 * 页面职责 = 「主区只看历史，配置收进弹窗」：
 * - 主区域只留评测历史列表（个人资产：仅本人 + ADMIN 可见——能不能对非本人库发起评测
 *   靠库的 eval ACL，不代表能看别人的评测记录）；
 * - 「创建评测」弹窗内完成全部配置：三个模型全部页自选（生成答案的对话模型 / RAGAS
 *   裁判 / answer_relevancy 相似度向量），不依赖库自身的 chat_model_id（需求：建库不
 *   指定对话模型，评测时才选）；知识库下拉按 eval ACL 过滤：只能看到本人库 + 被显式
 *   授权评测的非本人库；问答对动态行手填，或下载模板 / 导入 Excel 回显后再编辑；
 * - 弹窗内「执行投递」提交后投后台队列、关窗回列表，**不轮询**——历史列表「刷新」按钮
 *   手动拉状态与结果。
 *
 * 指标口径与后端 common_constants/rag_constant.py 第十一节静态对账（见 ./api.ts）。
 */
import type {
  EvalItemResp,
  EvalPair,
  EvalRunResp,
  ModelOption,
} from './api';
import type { KbOption } from '../doc/api';

import { computed, onMounted, ref, watch } from 'vue';

import { useAccess } from '@vben/access';

import {
  DownloadOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import {
  Alert,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  CreateEvalRun,
  DeleteEvalRun,
  DownloadEvalTemplate,
  EvalKbs,
  EVAL_METRIC_LABELS,
  EVAL_METRICS_ALL,
  EVAL_STATUS_ALL,
  EVAL_STATUS_COLORS,
  EVAL_STATUS_LABELS,
  EvalRunDetail,
  EvalRunPage,
  fetchEvalChatModels,
  fetchEvalEmbedModels,
  ImportEvalPairs,
  RerunEvalRun,
} from './api';
import {
  KG_DEPTH_MAX,
  KG_DEPTH_MIN,
  KG_SCALE_MAX,
  KG_SCALE_MIN,
} from '../doc/api';

const { hasPermission } = useAccess();
// 与后端 eval_router 上 @has_permission("ai:kb:eval") 同一位：没权限就不发请求，免得满屏 403
const allowed = computed(() => hasPermission('ai:kb:eval'));

// ==================== 一、评测配置（「创建评测」弹窗） ====================
/**
 * 创建弹窗开关。表单状态存在页面级 ref 里，关窗不重置：连跑几场只微调一两个参数
 * 是评测的常态，每开一次窗就清空一遍反而逼人重填全套。
 */
const createOpen = ref(false);

const kbOptions = ref<KbOption[]>([]);
const genModelOptions = ref<ModelOption[]>([]);
const judgeModelOptions = ref<ModelOption[]>([]);
const embedModelOptions = ref<ModelOption[]>([]);

const form = ref({
  kbId: undefined as number | undefined,
  name: '',
  generationModelId: undefined as number | undefined,
  judgeModelId: undefined as number | undefined,
  embedModelId: undefined as number | undefined,
  topK: 5,
  scoreThreshold: undefined as number | undefined,
  retrievalMode: undefined as string | undefined,
  withGraph: false,
  // 图谱增强那一路的深度/规模（区间与检索页同口径，后端 kg_search clamp 再夹一次）
  graphDepth: 2,
  graphScale: 10,
  graphSourceChunks: false,
});

const RETRIEVE_MODE_OPTIONS = [
  { label: '混合', value: 'HYBRID' },
  { label: '向量', value: 'VECTOR' },
  { label: '关键词', value: 'KEYWORD' },
];

// 问答对：动态行（问题 + 参考答案）
const pairs = ref<EvalPair[]>([{ question: '', reference: '' }]);

function addPair() {
  pairs.value.push({ question: '', reference: '' });
}

function removePair(idx: number) {
  pairs.value.splice(idx, 1);
  if (pairs.value.length === 0) pairs.value.push({ question: '', reference: '' });
}

async function loadKbs() {
  if (!allowed.value) return;
  try {
    kbOptions.value = await EvalKbs();
  } catch {
    kbOptions.value = [];
  }
}

async function loadModels() {
  // 拉不回来只是下拉为空，不阻断页面；三个下拉各自独立取，互不牵连
  try {
    const chats = await fetchEvalChatModels();
    genModelOptions.value = chats;
    judgeModelOptions.value = chats;
  } catch {
    genModelOptions.value = [];
    judgeModelOptions.value = [];
  }
  try {
    embedModelOptions.value = await fetchEvalEmbedModels();
  } catch {
    embedModelOptions.value = [];
  }
}

const kbSelectOptions = computed(() =>
  kbOptions.value.map((o) => ({
    label: `${o.name}（${o.kbTypeLabel ?? o.kbType}）`,
    value: o.id,
  })),
);

// 当前选中的是不是文档型库（图谱增强只对 doc 型有意义，非 doc 型把开关藏掉免得白勾）
const selectedKb = computed(() =>
  kbOptions.value.find((o) => o.id === form.value.kbId),
);
const graphAvailable = computed(() => selectedKb.value?.kbType === 'doc');

async function onDownloadTemplate() {
  try {
    await DownloadEvalTemplate();
  } catch (error: any) {
    message.error(error?.message || '模板下载失败');
  }
}

async function onImport(file: File) {
  try {
    const res = await ImportEvalPairs(file);
    pairs.value = res.pairs.map((p) => ({ question: p.question, reference: p.reference ?? '' }));
    message.success(`已导入 ${res.total} 个问答对，可继续编辑`);
  } catch (error: any) {
    message.error(error?.message || '导入失败');
  }
  return false; // 拦下 antd 默认上传，改走我们带鉴权头的接口
}

const submitting = ref(false);

function validateForm(): null | string {
  if (!form.value.kbId) return '请选择要评测的知识库';
  if (!form.value.generationModelId) return '请选择文本生成模型';
  if (!form.value.judgeModelId) return '请选择裁判模型';
  if (!form.value.embedModelId) return '请选择向量模型';
  const valid = pairs.value.filter((p) => (p.question || '').trim());
  if (valid.length === 0) return '请至少录入一个有效的问答对（问题不能为空）';
  return null;
}

async function onSubmit() {
  if (!allowed.value) {
    return message.warning('没有「知识评测」的菜单权限（ai:kb:eval）');
  }
  const err = validateForm();
  if (err) return message.warning(err);
  const valid = pairs.value.filter((p) => (p.question || '').trim());
  submitting.value = true;
  try {
    const res = await CreateEvalRun({
      kbId: form.value.kbId!,
      name: form.value.name.trim() || undefined,
      generationModelId: form.value.generationModelId!,
      judgeModelId: form.value.judgeModelId!,
      embedModelId: form.value.embedModelId!,
      pairs: valid.map((p) => ({
        question: p.question.trim(),
        reference: (p.reference || '').trim() || null,
      })),
      topK: form.value.topK,
      scoreThreshold: form.value.scoreThreshold,
      retrievalMode: form.value.retrievalMode,
      withGraph: graphAvailable.value ? form.value.withGraph : false,
      graphDepth: form.value.graphDepth,
      graphScale: form.value.graphScale,
      graphSourceChunks: form.value.graphSourceChunks,
    });
    message.success(`评测已提交（共 ${res.totalPairs} 个问答对），稍后点「刷新」查看结果`);
    // 成功才关窗：校验不过 / 接口报错都留在弹窗里就地改，不用重填一遍
    createOpen.value = false;
    await loadRuns();
  } catch (error: any) {
    message.error(error?.message || '评测提交失败');
  } finally {
    submitting.value = false;
  }
}

// ==================== 二、运行历史 ====================
const runs = ref<EvalRunResp[]>([]);
const runsLoading = ref(false);
const runFilter = ref({
  status: undefined as string | undefined,
  kbId: undefined as number | undefined,
});
const pagination = ref({ current: 1, pageSize: 10, total: 0 });

const statusOptions = EVAL_STATUS_ALL.map((s) => ({
  label: EVAL_STATUS_LABELS[s],
  value: s,
}));

async function loadRuns() {
  if (!allowed.value) return;
  runsLoading.value = true;
  try {
    const res = await EvalRunPage({
      current: pagination.value.current,
      size: pagination.value.pageSize,
      kbId: runFilter.value.kbId,
      status: runFilter.value.status,
    });
    runs.value = res.records;
    pagination.value.total = res.total;
  } catch (error: any) {
    message.error(error?.message || '加载评测历史失败');
    runs.value = [];
  } finally {
    runsLoading.value = false;
  }
}

function onTableChange(pg: any) {
  pagination.value.current = pg.current;
  pagination.value.pageSize = pg.pageSize;
  loadRuns();
}

// 筛选条件一变就查（回页码避免「条件变了还停在第 5 页」）；「查询」按钮兑同一套逻辑，
// 两者叠加也不会双发：Select 的变化频率低，直接查比防抖更跟手
watch(runFilter, () => {
  pagination.value.current = 1;
  loadRuns();
});

function resetFilter() {
  // 只负责把条件换回空（赋新对象必触发上面的 watch，查询由 watch 接管）
  runFilter.value = { status: undefined, kbId: undefined };
}

async function onDeleteRun(runId: number) {
  try {
    await DeleteEvalRun(runId);
    message.success('删除成功');
    await loadRuns();
  } catch (error: any) {
    message.error(error?.message || '删除失败');
  }
}

/** 重新执行一场已收口的评测：配置快照沿用，状态/耗时/指标在库里清零后重新投队列 */
async function onRerunRun(runId: number) {
  try {
    await RerunEvalRun(runId);
    message.success('已重新投递，稍后点「刷新」查看结果');
    await loadRuns();
  } catch (error: any) {
    message.error(error?.message || '重新执行失败');
  }
}

// 均值单元格：无值显示「—」，有值保留两位（评测分多为 0~1，三位以上没意义）
function fmtMetric(v: null | number | undefined): string {
  return v === null || v === undefined ? '—' : v.toFixed(2);
}

/**
 * 总耗时展示：一场评测几十秒到几分钟，原样毫秒没眼读——按档折算成 秒/分。
 */
function fmtDuration(ms: number | undefined): string {
  const v = Number(ms || 0);
  if (v >= 60_000) {
    const m = Math.floor(v / 60_000);
    const s = Math.round((v % 60_000) / 1000);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  if (v >= 1_000) return `${(v / 1000).toFixed(1)}s`;
  return `${v}ms`;
}

/**
 * 五个指标的一句人话口径（表头「?」悬停展示）：与 EVAL_METRIC_LABELS 同键，
 * 对齐 RAGAS 官方定义的通俗说法——公式留在文档里，表头只说「越高代表什么」。
 */
const EVAL_METRIC_HINTS: Record<string, string> = {
  faithfulness: '生成答案里的说法有多少能在召回原文中找到依据，越高代表编造越少',
  answer_relevancy: '答案与问题的相关程度，越高代表越切题、不答非所问',
  context_precision: '召回的原文中有多少条真正有用（越靠前权重越高），越高代表召回越干净',
  context_recall: '参考答案需要的信息有多少被召回原文覆盖，越高代表漏召回越少',
  answer_correctness: '生成答案与参考答案在事实与语义上的接近程度',
};

/**
 * 列表状态 Tag 的底色：后端 DONE 只保证「至少一条拿到分」，10 对里挂 9 对也照样是 DONE，
 * 原样染成绿色就等于列表把部分失败说成通过 → 有失败就降成橙色，让「完成」不再骗人。
 */
function runTagColor(record: EvalRunResp): string {
  if (record.status === 'DONE' && record.donePairs < record.totalPairs) {
    return 'warning';
  }
  return EVAL_STATUS_COLORS[record.status] || 'default';
}

/**
 * 还没收口的场次（排队中 / 评测中）：这一段后端不会中途写 done_pairs（只在收口时一次性
 * 落库），跑动中拉到的完成数必然是 0，所以进度不能拿它当百分比画。
 */
function isRunInFlight(record: EvalRunResp): boolean {
  return record.status === 'RUNNING' || record.status === 'PENDING';
}

/**
 * 分数什么时候要露出来：跑动中是进度，收口后没跑满则是「几对真正拿到了分」。
 */
function showDoneFraction(record: EvalRunResp): boolean {
  return isRunInFlight(record) || record.donePairs < record.totalPairs;
}

/** 进度条填充比例 = 拿到分的条数占比（与状态 Tag 同一口径，不另算一份） */
function runBarPercent(record: EvalRunResp): number {
  if (!record.totalPairs) return 0;
  return Math.round((record.donePairs / record.totalPairs) * 100);
}

/** 条色跟着状态走，避免出现「绿标签配红条」这种自相矛盾的行 */
function runBarClass(record: EvalRunResp): string {
  if (record.status === 'RUNNING') return 'is-running';
  // 排队中：worker 还没取走，给一条空轨而不是流动条（流动会被读成「已经在跑了」）
  if (record.status === 'PENDING') return 'is-queued';
  if (record.status === 'FAILED') return 'is-failed';
  if (record.donePairs < record.totalPairs) return 'is-partial';
  return 'is-full';
}

const runColumns = [
  { title: '运行', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
  { title: '知识库', dataIndex: 'kbName', key: 'kbName', width: 140, ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 150 },
  { title: '问答对', dataIndex: 'totalPairs', key: 'totalPairs', width: 100 },
  { title: '指标分数', key: 'metrics', width: 260 },
  { title: '总耗时', dataIndex: 'totalLatencyMs', key: 'totalLatencyMs', width: 100 },
  { title: '创建时间', dataIndex: 'createTime', key: 'createTime', width: 160 },
  { title: '操作', key: 'action', width: 170, fixed: 'right' },
];

// ==================== 三、详情 ====================
const detailOpen = ref(false);
const detailLoading = ref(false);
const detail = ref<EvalRunResp | null>(null);

const itemColumns = [
  { title: '#', dataIndex: 'itemId', key: 'itemId', width: 60 },
  { title: '问题', dataIndex: 'question', key: 'question', width: 220, ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  { title: '忠实度', dataIndex: 'faithfulness', key: 'faithfulness', width: 80 },
  { title: '答案相关性', dataIndex: 'answerRelevancy', key: 'answerRelevancy', width: 100 },
  { title: '上下文精确率', dataIndex: 'contextPrecision', key: 'contextPrecision', width: 110 },
  { title: '上下文召回率', dataIndex: 'contextRecall', key: 'contextRecall', width: 110 },
  { title: '答案正确性', dataIndex: 'answerCorrectness', key: 'answerCorrectness', width: 100 },
  { title: '召回/生成/打分耗时 (ms)', key: 'took', width: 170 },
];

async function openDetail(runId: number) {
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await EvalRunDetail(runId);
  } catch (error: any) {
    message.error(error?.message || '加载详情失败');
  } finally {
    detailLoading.value = false;
  }
}

onMounted(() => {
  loadKbs();
  loadModels();
  loadRuns();
});
</script>

<template>
  <div class="page-container">
    <Alert
      v-if="!allowed"
      type="warning"
      show-icon
      class="mb-2"
      message="没有「知识评测」的菜单权限（ai:kb:eval），本页不可用。"
    />

    <!-- 主区域唯一的卡：评测历史（全部配置已收进「创建评测」弹窗）。头部不再单独占一
         行标题：工具区（创建 + 筛选 + 查询/重置/刷新）直接放 #title 槽靠左对齐，多出来的
         那一行高度让给表格 -->
    <Card :bordered="false" class="eval-history-card">
      <template #title>
        <Space :size="8" wrap>
          <Button type="primary" @click="createOpen = true">
            <template #icon><PlusOutlined /></template>
            创建评测
          </Button>
          <Select
            v-model:value="runFilter.kbId"
            :options="kbSelectOptions"
            placeholder="按库筛选"
            style="min-width: 160px"
            allow-clear
          />
          <Select
            v-model:value="runFilter.status"
            :options="statusOptions"
            placeholder="按状态筛选"
            style="min-width: 120px"
            allow-clear
          />
          <Button :loading="runsLoading" @click="loadRuns">
            <template #icon><SearchOutlined /></template>
            查询
          </Button>
          <Button @click="resetFilter">重置</Button>
          <Button :loading="runsLoading" @click="loadRuns">
            <template #icon><ReloadOutlined /></template>
            刷新
          </Button>
        </Space>
      </template>

      <Table
        :columns="runColumns"
        :data-source="runs"
        :loading="runsLoading"
        :pagination="pagination"
        row-key="runId"
        size="small"
        :scroll="{ x: 1220, y: 400 }"
        @change="onTableChange"
      >
        <!-- 列头定制：指标分数带「?」悬停讲五个指标的口径。其余列必须兜底回 title，
             否则 headerCell 插槽一旦存在，没写分支的列头会整体变空白 -->
        <template #headerCell="{ column, title }">
          <template v-if="column.key === 'metrics'">
            指标分数
            <Tooltip>
              <template #title>
                <div v-for="m in EVAL_METRICS_ALL" :key="m" class="metric-help-line">
                  <b>{{ EVAL_METRIC_LABELS[m] }}</b>：{{ EVAL_METRIC_HINTS[m] }}
                </div>
              </template>
              <QuestionCircleOutlined class="metric-help-icon" />
            </Tooltip>
          </template>
          <template v-else>{{ title }}</template>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <!-- 失败原因挂在状态 Tag 上：runner 会把「N 条失败 + 首条原因」写进 run.error，
                 只靠点进详情才看得到，列表里一排绿标没人会去点 -->
            <Tooltip :title="record.error || ''">
              <Tag :color="runTagColor(record)">
                {{
                  record.statusLabel ||
                  EVAL_STATUS_LABELS[record.status] ||
                  record.status
                }}
              </Tag>
            </Tooltip>
            <div class="run-progress-row">
              <div class="run-progress" :class="runBarClass(record)">
                <div
                  v-if="!isRunInFlight(record)"
                  class="run-progress-fill"
                  :style="{ width: `${runBarPercent(record)}%` }"
                ></div>
              </div>
              <span v-if="showDoneFraction(record)" class="done-hint">
                {{ record.donePairs }}/{{ record.totalPairs }}
              </span>
            </div>
          </template>
          <template v-else-if="column.key === 'totalPairs'">
            {{ record.totalPairs }}
          </template>
          <template v-else-if="column.key === 'metrics'">
            <Space v-if="record.status === 'DONE'" direction="vertical" :size="0">
              <span v-for="m in EVAL_METRICS_ALL" :key="m" class="metric-line">
                {{ EVAL_METRIC_LABELS[m] }}:
                <b>{{ fmtMetric(record.metricAvgs?.[m]) }}</b>
              </span>
            </Space>
            <span v-else class="param-label">—</span>
          </template>
          <template v-else-if="column.key === 'totalLatencyMs'">
            {{ fmtDuration(record.totalLatencyMs) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Space :size="4">
              <Button size="small" type="link" @click="openDetail(record.runId)">详情</Button>
              <!-- 重新执行：配置快照沿用，状态/耗时/指标清零后重新投队列；
                   还在排队/跑动中的场次后端会拦，这里先不把入口露出来 -->
              <Popconfirm
                v-if="record.status === 'DONE' || record.status === 'FAILED'"
                title="重新执行这场评测？"
                description="沿用当时的配置快照，已有结果与指标全部清零重跑"
                ok-text="重跑"
                cancel-text="取消"
                @confirm="onRerunRun(record.runId)"
              >
                <Button size="small" type="link">重跑</Button>
              </Popconfirm>
              <Popconfirm
                title="删除这条评测记录？"
                ok-text="删除"
                cancel-text="取消"
                @confirm="onDeleteRun(record.runId)"
              >
                <Button size="small" type="link" danger>删除</Button>
              </Popconfirm>
            </Space>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 四、详情抽屉 -->
    <Drawer
      v-model:open="detailOpen"
      title="评测详情"
      width="90%"
      placement="right"
      :body-style="{ paddingTop: '12px' }"
    >
      <Spin :spinning="detailLoading">
        <template v-if="detail">
          <!-- run.error 现在也装「部分失败」的说明（终态是 DONE 但有条项没拿到分）：
               整单失败才用红色，免得把 9/10 成功渲染成一整条报错 -->
          <Alert
            v-if="detail.error"
            :type="detail.status === 'FAILED' ? 'error' : 'warning'"
            show-icon
            class="mb-2"
            :message="detail.error"
          />
          <Card size="small" class="mb-2">
            <Space wrap :size="16">
              <span><span class="param-label">运行：</span>{{ detail.name }}</span>
              <span><span class="param-label">知识库：</span>{{ detail.kbName }}</span>
              <span>
                <span class="param-label">状态：</span>
                <Tag :color="runTagColor(detail)">
                  {{ detail.statusLabel || detail.status }}
                </Tag>
              </span>
              <span><span class="param-label">问答对：</span>{{ detail.donePairs }}/{{ detail.totalPairs }}</span>
              <span><span class="param-label">TopK：</span>{{ detail.topK }}</span>
              <span><span class="param-label">阈值：</span>{{ detail.scoreThreshold }}</span>
              <span><span class="param-label">模式：</span>{{ detail.retrievalMode || '默认' }}</span>
              <span>
                <span class="param-label">图谱增强：</span>
                {{
                  detail.withGraph
                    ? `开（深度 ${detail.graphDepth} · 规模 ${detail.graphScale}）`
                    : '关'
                }}
              </span>
              <span>
                <span class="param-label">总耗时：</span>{{ fmtDuration(detail.totalLatencyMs) }}
              </span>
              <span><span class="param-label">创建时间：</span>{{ detail.createTime }}</span>
            </Space>
            <div class="detail-avgs">
              <span v-for="m in EVAL_METRICS_ALL" :key="m" class="metric-chip">
                {{ EVAL_METRIC_LABELS[m] }}
                <b>{{ fmtMetric(detail.metricAvgs?.[m]) }}</b>
              </span>
            </div>
          </Card>

          <Table
            :columns="itemColumns"
            :data-source="detail.items || []"
            row-key="itemId"
            size="small"
            :pagination="false"
            :scroll="{ x: 1100 }"
          >
            <template #bodyCell="{ column, record: it }">
              <template v-if="column.key === 'status'">
                <Tag :color="EVAL_STATUS_COLORS[it.status] || 'default'">{{ it.statusLabel || it.status }}</Tag>
              </template>
              <template v-else-if="['faithfulness', 'answerRelevancy', 'contextPrecision', 'contextRecall', 'answerCorrectness'].includes(column.key)">
                {{ fmtMetric(it[column.key]) }}
              </template>
              <template v-else-if="column.key === 'took'">
                {{ it.tookRecallMs }} / {{ it.tookGenerateMs }} / {{ it.tookScoreMs }}
              </template>
            </template>
            <!-- 展开入口只有 antd 因本插槽自动加上的行首箭头，不再另摆一个「展开」按钮列 -->
            <template #expandedRowRender="{ record: it }">
              <div class="item-detail">
                <div v-if="it.error" class="item-error">错误：{{ it.error }}</div>
                <div class="item-block">
                  <div class="item-label">问题</div>
                  <div class="item-text">{{ it.question }}</div>
                </div>
                <div class="item-block">
                  <div class="item-label">参考答案</div>
                  <div class="item-text">{{ it.reference || '（无）' }}</div>
                </div>
                <div class="item-block">
                  <div class="item-label">生成答案</div>
                  <div class="item-text">{{ it.generatedAnswer || '（无）' }}</div>
                </div>
                <div class="item-block">
                  <div class="item-label">召回片段（{{ (it.contexts || []).length }} 条）</div>
                  <div v-if="(it.contexts || []).length" class="ctx-list">
                    <div v-for="(c, ci) in it.contexts" :key="ci" class="ctx-item">
                      <Tag v-if="c.recall" color="purple">{{ c.recall }}</Tag>
                      <Tag v-if="c.score != null" color="green">score {{ c.score }}</Tag>
                      <div class="ctx-text">{{ c.content }}</div>
                    </div>
                  </div>
                  <Empty v-else :image="Empty.PRESENTED_IMAGE_SIMPLE" description="没有召回片段" />
                </div>
              </div>
            </template>
          </Table>
        </template>
        <Empty v-else-if="!detailLoading" description="没有详情数据" />
      </Spin>
    </Drawer>

    <!-- 创建评测弹窗：原主区配置表单整体搬入（字段与提交逻辑不变，只是换了住处）。
         表单状态存在页面级 ref，关窗不清空：连跑几场只微调一两个参数是评测常态。 -->
    <Modal
      v-model:open="createOpen"
      title="创建评测"
      width="min(960px, 96vw)"
      :body-style="{ paddingTop: '8px' }"
    >
      <div class="create-form">
        <!-- 参数区 12 列栅格：第一行「评什么」（库 / 名称 / TopK / 阈值 4+2+2+4），
             第二行「怎么召回」（模式 + 图谱增强 4+8，图谱增强常驻、开了以后深度/规模/
             反查同格内排开），第三行「用哪三个模型」（4×3）。
             span 按 960px 弹窗宽度定，窄窗由样式里的 @media 降档成两列。 -->
        <div class="param-grid">
          <div class="param-item item-kb">
            <span class="param-label">知识库</span>
            <Select
              v-model:value="form.kbId"
              :options="kbSelectOptions"
              placeholder="选择被评测的知识库"
              class="grow"
              show-search
              option-filter-prop="label"
              allow-clear
            />
          </div>
          <div class="param-item item-name">
            <span class="param-label">运行名称</span>
            <Input
              v-model:value="form.name"
              placeholder="留空 = 库名 + 评测"
              class="grow"
              :maxlength="255"
            />
          </div>
          <!-- 第一行余下的参数位：TopK 与阈值（2+4）。「运行名称」缩到 span 2 后，阈值
               拿到多出的那 1 格（它的标签最长，原先 span 3 时 label 被挤得断行） -->
          <div class="param-item item-topk">
            <span class="param-label">TopK</span>
            <InputNumber
              v-model:value="form.topK"
              :min="1"
              :max="100"
              class="grow"
            />
          </div>
          <div class="param-item item-threshold">
            <Tooltip title="留空 = 用知识库自己的检索配置">
              <span class="param-label">相似度阈值</span>
            </Tooltip>
            <InputNumber
              v-model:value="form.scoreThreshold"
              :min="0"
              :max="1"
              :step="0.05"
              placeholder="默认"
              class="grow"
            />
          </div>
          <div class="param-item item-mode">
            <span class="param-label">检索模式</span>
            <Select
              v-model:value="form.retrievalMode"
              :options="RETRIEVE_MODE_OPTIONS"
              placeholder="默认"
              class="grow"
              allow-clear
            />
          </div>
          <!-- 图谱增强常驻展示：不再等选了 doc 库才出现（免得用户以为没有这项配置）。
               选了非 doc 库时开关置灰（图谱那一路只对文档型生效），提交时也会被强制关掉 -->
          <div class="param-item item-graph">
            <Tooltip
              title="叠加图谱检索那一路（仅文档型库生效、需已构建图谱）：开启后可配深度/规模与反查开关"
            >
              <span class="param-label">图谱增强</span>
            </Tooltip>
            <Switch
              v-model:checked="form.withGraph"
              :disabled="!!form.kbId && !graphAvailable"
            />
            <template v-if="form.withGraph">
              <Tooltip title="从命中的实体向外扩几跳（1-3）">
                <span class="param-label">深度</span>
              </Tooltip>
              <InputNumber
                v-model:value="form.graphDepth"
                :min="KG_DEPTH_MIN"
                :max="KG_DEPTH_MAX"
                style="width: 64px"
              />
              <Tooltip title="实体/关系各一路的向量召回条数（1-50）">
                <span class="param-label">规模</span>
              </Tooltip>
              <InputNumber
                v-model:value="form.graphScale"
                :min="KG_SCALE_MIN"
                :max="KG_SCALE_MAX"
                style="width: 78px"
              />
              <Tooltip title="图谱那一路是否顺命中实体反查原文切片叠进上下文">
                <span class="param-label">反查原文</span>
              </Tooltip>
              <Switch v-model:checked="form.graphSourceChunks" size="small" />
            </template>
            <span
              v-if="form.kbId && !graphAvailable"
              class="param-label graph-disabled-hint"
            >
              仅文档型知识库生效，已自动忽略
            </span>
          </div>
          <div class="param-item item-model">
            <Tooltip title="召回后拼 prompt 生成答案的对话模型（文生文）">
              <span class="param-label">文本生成模型</span>
            </Tooltip>
            <Select
              v-model:value="form.generationModelId"
              :options="genModelOptions"
              placeholder="生成答案的对话模型"
              class="grow"
              show-search
              option-filter-prop="label"
              allow-clear
            />
          </div>
          <div class="param-item item-model">
            <Tooltip title="answer_relevancy 用文本向量算问题与反向生成问题的相似度">
              <span class="param-label">向量模型</span>
            </Tooltip>
            <Select
              v-model:value="form.embedModelId"
              :options="embedModelOptions"
              placeholder="相似度向量模型"
              class="grow"
              show-search
              option-filter-prop="label"
              allow-clear
            />
          </div>
          <div class="param-item item-model">
            <Tooltip
              title="RAGAS 打分裁判模型（文生文）：忠实度 / 上下文精确率 / 召回率 / 答案正确性都靠它"
            >
              <span class="param-label">裁判模型</span>
            </Tooltip>
            <Select
              v-model:value="form.judgeModelId"
              :options="judgeModelOptions"
              placeholder="RAGAS 裁判模型"
              class="grow"
              show-search
              option-filter-prop="label"
              allow-clear
            />
          </div>
        </div>

        <!-- 问答对数据集 -->
        <div class="pairs-head">
          <span class="pairs-title">问答对（问题 + 参考答案）</span>
          <Space :size="8">
            <Button size="small" @click="onDownloadTemplate">
              <template #icon><DownloadOutlined /></template>
              下载模板
            </Button>
            <Upload :before-upload="onImport" :show-upload-list="false" accept=".xlsx,.xlsm">
              <Button size="small">
                <template #icon><UploadOutlined /></template>
                导入 Excel
              </Button>
            </Upload>
            <Button size="small" type="primary" @click="addPair">
              <template #icon><PlusOutlined /></template>
              添加一行
            </Button>
          </Space>
        </div>

        <div class="pairs-list">
          <div v-for="(p, idx) in pairs" :key="idx" class="pair-row">
            <span class="pair-no">{{ idx + 1 }}</span>
            <Input
              v-model:value="p.question"
              placeholder="问题"
              :maxlength="2000"
              class="grow"
            />
            <Input.TextArea
              v-model:value="p.reference"
              placeholder="参考答案（标准答案）"
              :auto-size="{ minRows: 1, maxRows: 3 }"
              :maxlength="2000"
              class="grow"
            />
            <Button size="small" danger @click="removePair(idx)">删除</Button>
          </div>
        </div>

        <span class="param-label">
          提交后投后台队列执行，页面不轮询，关窗后在历史列表点「刷新」查看结果
        </span>
      </div>

      <template #footer>
        <Button @click="createOpen = false">取消</Button>
        <Button type="primary" :loading="submitting" @click="onSubmit">执行投递</Button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
/* 评测还在跑时的进度条动画（用在下面 .run-progress.is-running 的滑动段）：
   stylelint 的 order 要求 at-rule 排在所有 rule 前面，故放在样式块开头 */
@keyframes run-progress-slide {
  from {
    transform: translateX(-100%);
  }

  to {
    transform: translateX(313%);
  }
}

/* 整页不滚：高度锁在视口里（140px 是布局顶栏 + 页签 + 内边距的占位，与 chat 页同一口径），
   配置收进弹窗后主区只剩历史卡；表体高度由上面 .ant-table-body 的覆盖规则吃满剩余空间，
   底部不再留一条用不上的白边。 */
.page-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  padding: 8px;
  overflow: hidden;
}

/* 历史卡占满主区剩下的高度，表体自己滚（配合 Table 的 scroll.y，表头不会跟着滚走）。
   表体高度不再用死的 400px：y=400 只是 antd 需要一个值才肯钉表头，这里用 CSS 覆写
   成随视口伸缩（底部剩余空间全部让给表体），260 底价防小屏压塌 */
.eval-history-card {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.eval-history-card :deep(.ant-card-body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

/* 覆盖 scroll.y 生成的内联 max-height（内联优先级高，必须 !important） */
.eval-history-card :deep(.ant-table-body) {
  max-height: calc(100vh - 320px) !important;
  min-height: 260px;
}

/* 「创建评测」弹窗体：问答对可能一填几十行，封一个高度上限让弹窗内部自己滚，
   遮罩后面的页面不受影响；padding-right 给滚动条让位，最后一列不被条压住 */
.create-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: calc(100vh - 240px);
  overflow-y: auto;
  padding-right: 4px;
}

/* 参数区 12 列栅格（与检索页搜索栏同一口径）：控件多的表单靠 Space wrap 折行
   上下对不齐，栅格 span 让每行起点一致 */
.param-grid {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 12px;
}

.param-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 条目里的控件吃满剩余宽度：不用逐个 inline min-width，窄窗交给栅格降档统一收 */
.create-form .grow {
  flex: 1 1 auto;
  min-width: 0;
}

/* 第一行「评什么」：库 / 名称 / TopK / 阈值（4+2+2+4 = 12）。名称缩到 2 格
   （输入框短一点，label 不再被挤得断行），阈值拿多出的那 1 格 */
.item-kb {
  grid-column: span 4;
}

.item-name {
  grid-column: span 2;
}

.item-topk {
  grid-column: span 2;
}

.item-threshold {
  grid-column: span 4;
}

/* 第二行「怎么召回」：检索模式 + 图谱增强开关（4+8 = 12）；非 doc 库时占位提示同一格 */
.item-mode {
  grid-column: span 4;
}

.item-graph {
  flex-wrap: wrap;
  grid-column: span 8;
}

/* 第三行「用哪三个模型」：等分三份（4×3 = 12），长下拉互相不挤 */
.item-model {
  grid-column: span 4;
}

.param-label {
  /* 标签永不折行：格子被窄窗挤住时先缩控件，不能让「运行名称」四个字竖着排 */
  flex: none;
  font-size: 12px;
  color: var(--text-color-secondary, #888);
  white-space: nowrap;
}

/* 选了非文档型库时贴在开关旁的说明：窄格里就地收进省略，不把格子撑高 */
.graph-disabled-hint {
  overflow: hidden;
  text-overflow: ellipsis;
}

.pairs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.pairs-title {
  font-size: 13px;
  font-weight: 600;
}

.pairs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.pair-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.pair-no {
  width: 24px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
  text-align: right;
  line-height: 32px;
}

.metric-line {
  font-size: 12px;
}

/* 表头「?」图标：小一号、次色，悬停变主色；Tooltip 里的逐条说明行留点行距好读 */
.metric-help-icon {
  margin-left: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
  cursor: help;
}

.metric-help-icon:hover {
  color: var(--primary-color, #1677ff);
}

/* Tooltip 弹层挂在 body 下，:deep 的前缀式命中不了它；但 slot 内容的元素本身
   带着本组件的 data-v，直接用相邻兄弟选择器就能命中 */
.metric-help-line + .metric-help-line {
  margin-top: 4px;
}

.done-hint {
  margin-left: 6px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

/* 状态列的进度条：填充段 = 拿到分的条数占比（与 Tag 的 warning/error 同一口径）。
   评测中不画百分比（后端要收口时才写数），改成一条来回滑动的蓝段，只表达「还在跑」。 */
.run-progress-row {
  display: flex;
  align-items: center;
  margin-top: 4px;
}

.run-progress {
  position: relative;
  width: 76px;
  height: 5px;
  overflow: hidden;
  background: var(--border-color, #eee);
  border-radius: 3px;
}

.run-progress-fill {
  height: 100%;
  border-radius: 3px;
}

.run-progress.is-full .run-progress-fill {
  background: #52c41a;
}

.run-progress.is-partial .run-progress-fill {
  background: #faad14;
}

/* 整单失败时后端必然写 done_pairs=0，填充段宽度为 0 等于什么都不画 → 红改画在轨道上，
   否则失败行会和排队行看起来一模一样（都是一条空灰轨），失败就没报到页面上 */
.run-progress.is-failed {
  background: #ff4d4f;
}

.run-progress.is-running::after {
  position: absolute;
  top: 0;
  left: 0;
  width: 32%;
  height: 100%;
  content: '';
  background: #1677ff;
  border-radius: 3px;
  animation: run-progress-slide 1.2s ease-in-out infinite;
}

.detail-avgs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.metric-chip {
  padding: 2px 10px;
  font-size: 12px;
  background: var(--fill-quaternary, #f5f5f5);
  border-radius: 12px;
}

.item-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.item-error {
  color: #cf1322;
}

.item-label {
  margin-bottom: 2px;
  font-size: 12px;
  color: var(--text-color-secondary, #888);
}

.item-text {
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}

.ctx-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ctx-item {
  padding: 6px 8px;
  background: var(--fill-quaternary, #fafafa);
  border-radius: 4px;
}

.ctx-text {
  margin-top: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 窄窗降档：参数条目全部并成两列，控件不至于挤成一条缝（与检索页同一断点思路） */
@media (max-width: 1000px) {
  .item-kb,
  .item-name,
  .item-topk,
  .item-threshold,
  .item-mode,
  .item-graph,
  .item-model {
    grid-column: span 6;
  }
}
</style>
