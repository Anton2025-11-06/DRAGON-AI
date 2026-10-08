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
  if (!form.value.generationModelId) return '请选择生成答案的对话模型';
  if (!form.value.judgeModelId) return '请选择裁判模型';
  if (!form.value.embedModelId) return '请选择相似度向量模型';
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

const runColumns = [
  { title: '运行', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
  { title: '知识库', dataIndex: 'kbName', key: 'kbName', width: 140, ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
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
  { title: '展开', key: 'expand', width: 80 },
];

const expandedItems = ref<Set<number>>(new Set());

function toggleItem(itemId: number) {
  const next = new Set(expandedItems.value);
  if (next.has(itemId)) next.delete(itemId);
  else next.add(itemId);
  expandedItems.value = next;
}

async function openDetail(runId: number) {
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  expandedItems.value = new Set();
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
    <Card :bordered="false" title="评测配置" class="mb-2">
      <Space direction="vertical" :size="12" style="width: 100%">
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
          <Tooltip title="召回后拼 prompt 生成答案的对话模型（文生文）">
            <span class="param-label">生成模型</span>
          </Tooltip>
          <Select
            v-model:value="form.generationModelId"
            :options="genModelOptions"
            placeholder="生成答案的对话模型"
            style="min-width: 200px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
          <Tooltip title="RAGAS 打分裁判模型（文生文）：忠实度 / 上下文精确率 / 召回率 / 答案正确性都靠它">
            <span class="param-label">裁判模型</span>
          </Tooltip>
          <Select
            v-model:value="form.judgeModelId"
            :options="judgeModelOptions"
            placeholder="RAGAS 裁判模型"
            style="min-width: 200px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
          <Tooltip title="answer_relevancy 用文本向量算问题与反向生成问题的相似度">
            <span class="param-label">相似度向量</span>
          </Tooltip>
          <Select
            v-model:value="form.embedModelId"
            :options="embedModelOptions"
            placeholder="相似度向量模型"
            style="min-width: 200px"
            show-search
            option-filter-prop="label"
            allow-clear
          />
        </Space>

        <Space wrap :size="12">
          <span class="param-label">TopK</span>
          <InputNumber v-model:value="form.topK" :min="1" :max="100" style="width: 78px" />
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
            <Tooltip title="叠加图谱增强那一路召回（仅文档型；开启需要该库已构建图谱）">
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
    <Card :bordered="false" title="评测历史" class="mb-2">
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
        :scroll="{ x: 1100 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <Tag :color="EVAL_STATUS_COLORS[record.status] || 'default'">
              {{ record.statusLabel || EVAL_STATUS_LABELS[record.status] || record.status }}
            </Tag>
            <span v-if="record.status === 'RUNNING' || record.status === 'PENDING'" class="done-hint">
              {{ record.donePairs }}/{{ record.totalPairs }}
            </span>
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
          <Alert
            v-if="detail.error"
            type="error"
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
                <Tag :color="EVAL_STATUS_COLORS[detail.status] || 'default'">
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
              <template v-else-if="column.key === 'expand'">
                <Button size="small" type="link" @click="toggleItem(it.itemId)">
                  {{ expandedItems.has(it.itemId) ? '收起' : '展开' }}
                </Button>
              </template>
            </template>
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
.page-container {
  padding: 8px;
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
