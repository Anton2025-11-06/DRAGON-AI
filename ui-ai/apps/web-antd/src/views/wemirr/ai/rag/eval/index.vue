<script lang="ts" setup name="RagKnowledgeEval">
/**
 * 知识评测页（RAGAS）：指定知识库 + 问答对数据集，跑五指标 + 三段耗时评估召回与问答质量。
 *
 * 页面职责 = 「配一次评测、看历史结果」：
 * - 三个模型全部页自选（生成答案的对话模型 / RAGAS 裁判 / answer_relevancy 相似度向量），
 *   不依赖库自身的 chat_model_id（需求：建库不指定对话模型，评测时才选）；
 * - 知识库下拉按 eval ACL 过滤：只能看到本人库 + 被显式授权评测的非本人库；
 * - 问答对：动态行手填，或下载模板 / 导入 Excel 回显后再编辑；
 * - 「开始评测」提交后投后台队列即返回，**不轮询**——列表给「刷新」按钮手动拉状态与结果；
 * - 历史是个人资产：仅本人 + ADMIN 可见（能不能对非本人库发起评测靠库的 eval ACL，
 *   不代表能看别人的评测记录）。
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

import { computed, onMounted, ref } from 'vue';

import { useAccess } from '@vben/access';

import {
  DownloadOutlined,
  PlusOutlined,
  ReloadOutlined,
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
} from './api';

const { hasPermission } = useAccess();
// 与后端 eval_router 上 @has_permission("ai:kb:eval") 同一位：没权限就不发请求，免得满屏 403
const allowed = computed(() => hasPermission('ai:kb:eval'));

// ==================== 一、评测配置 ====================
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
      graphSourceChunks: form.value.graphSourceChunks,
    });
    message.success(`评测已提交（共 ${res.totalPairs} 个问答对），稍后点「刷新」查看结果`);
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

function resetFilter() {
  runFilter.value = { status: undefined, kbId: undefined };
  pagination.value.current = 1;
  loadRuns();
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

// 均值单元格：无值显示「—」，有值保留两位（评测分多为 0~1，三位以上没意义）
function fmtMetric(v: null | number | undefined): string {
  return v === null || v === undefined ? '—' : v.toFixed(2);
}

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
  { title: '五指标均值', key: 'metrics', width: 260 },
  { title: '平均耗时', dataIndex: 'avgLatencyMs', key: 'avgLatencyMs', width: 100 },
  { title: '创建时间', dataIndex: 'createTime', key: 'createTime', width: 160 },
  { title: '操作', key: 'action', width: 130, fixed: 'right' },
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
  { title: '召回/生成/打分 (ms)', key: 'took', width: 160 },
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

    <!-- 一、评测配置 -->
    <Card :bordered="false" title="评测配置" class="eval-config-card mb-2">
      <Space direction="vertical" :size="12" style="width: 100%">
        <!-- 配置区两行：第一行评什么 + 怎么召回（库 / 运行名称 / TopK / 阈值 / 检索模式 /
        图谱增强），第二行用哪三个模型（生成 / 向量 / 裁判）。模型都是 min-width 220 的下拉，
        再并到第一行就会超出常规屏宽，所以留它单独一行；窗口不够宽时 Space 的 wrap 自行折行。 -->
        <Space wrap :size="12">
          <span class="param-label">知识库</span>
          <Select
            v-model:value="form.kbId"
            :options="kbSelectOptions"
            placeholder="选择被评测的知识库"
            style="min-width: 240px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
          <span class="param-label">运行名称</span>
          <Input
            v-model:value="form.name"
            placeholder="留空 = 库名 + 评测"
            style="width: 200px"
            :maxlength="255"
          />
          <span class="param-label">TopK</span>
          <InputNumber
            v-model:value="form.topK"
            :min="1"
            :max="100"
            style="width: 78px"
          />
          <Tooltip title="留空 = 用知识库自己的检索配置">
            <span class="param-label">相似度阈值</span>
          </Tooltip>
          <InputNumber
            v-model:value="form.scoreThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            placeholder="默认"
            style="width: 92px"
          />
          <span class="param-label">检索模式</span>
          <Select
            v-model:value="form.retrievalMode"
            :options="RETRIEVE_MODE_OPTIONS"
            placeholder="默认"
            style="min-width: 120px"
            allow-clear
          />
          <template v-if="graphAvailable">
            <Tooltip
              title="叠加图谱增强那一路召回（仅文档型；开启需要该库已构建图谱）"
            >
              <span class="param-label">图谱增强</span>
            </Tooltip>
            <Switch v-model:checked="form.withGraph" />
            <template v-if="form.withGraph">
              <Tooltip title="图谱那一路是否顺命中实体反查原文切片叠进上下文">
                <span class="param-label">反查原文</span>
              </Tooltip>
              <Switch v-model:checked="form.graphSourceChunks" size="small" />
            </template>
          </template>
          <span v-else-if="form.kbId" class="param-label">
            图谱增强仅文档型知识库可用
          </span>
        </Space>

        <Space wrap :size="12">
          <Tooltip title="召回后拼 prompt 生成答案的对话模型（文生文）">
            <span class="param-label">文本生成模型</span>
          </Tooltip>
          <Select
            v-model:value="form.generationModelId"
            :options="genModelOptions"
            placeholder="生成答案的对话模型"
            style="min-width: 220px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
          <Tooltip
            title="answer_relevancy 用文本向量算问题与反向生成问题的相似度"
          >
            <span class="param-label">向量模型</span>
          </Tooltip>
          <Select
            v-model:value="form.embedModelId"
            :options="embedModelOptions"
            placeholder="相似度向量模型"
            style="min-width: 220px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
          <Tooltip
            title="RAGAS 打分裁判模型（文生文）：忠实度 / 上下文精确率 / 召回率 / 答案正确性都靠它"
          >
            <span class="param-label">裁判模型</span>
          </Tooltip>
          <Select
            v-model:value="form.judgeModelId"
            :options="judgeModelOptions"
            placeholder="RAGAS 裁判模型"
            style="min-width: 220px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
        </Space>

        <!-- 二、问答对数据集 -->
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
              style="flex: 1"
            />
            <Input.TextArea
              v-model:value="p.reference"
              placeholder="参考答案（标准答案）"
              :auto-size="{ minRows: 1, maxRows: 3 }"
              :maxlength="2000"
              style="flex: 1"
            />
            <Button size="small" danger @click="removePair(idx)">删除</Button>
          </div>
        </div>

        <Space>
          <Button type="primary" :loading="submitting" @click="onSubmit">开始评测</Button>
          <span class="param-label">提交后投后台队列执行，页面不轮询，稍后点下方「刷新」看结果</span>
        </Space>
      </Space>
    </Card>

    <!-- 三、运行历史 -->
    <Card :bordered="false" title="评测历史" class="eval-history-card mb-2">
      <template #extra>
        <Space :size="8">
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
          <Button size="small" @click="resetFilter">重置</Button>
          <Button size="small" type="primary" :loading="runsLoading" @click="loadRuns">
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
          <template v-else-if="column.key === 'avgLatencyMs'">
            {{ record.avgLatencyMs || 0 }} ms
          </template>
          <template v-else-if="column.key === 'action'">
            <Space :size="4">
              <Button size="small" type="link" @click="openDetail(record.runId)">详情</Button>
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
              <span><span class="param-label">图谱增强：</span>{{ detail.withGraph ? '开' : '关' }}</span>
              <span><span class="param-label">平均耗时：</span>{{ detail.avgLatencyMs || 0 }} ms</span>
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
   滚动只发生在配置块内部和历史框内部，所以配置栏永远钉在原地不会上下跑。 */
.page-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  padding: 8px;
  overflow: hidden;
}

/* 配置卡不参与长高：问答对行数多时它自己滚，最多占半屏，
   不会把下面的历史表顶到看不见的地方 */
.eval-config-card {
  display: flex;
  flex: none;
  flex-direction: column;
  max-height: 52%;
  overflow: hidden;
}

.eval-config-card :deep(.ant-card-body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

/* 历史卡固定占满剩下的高度，表体自己滚（配合 Table 的 scroll.y，表头不会跟着滚走） */
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

.param-label {
  font-size: 12px;
  color: var(--text-color-secondary, #888);
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
</style>
