<script lang="ts" setup name="ArqMonitorPage">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import { Card, InputNumber, message, Select, Switch } from 'ant-design-vue';

import { defHttp } from '#/api/request';

// ==================== ARQ 分片队列监控（工作流 / 知识库双流水线） ====================
// 数据源：
//   GET /api/system/arq/pipelines    可监控的流水线清单（下拉选项，后端注册表同源）
//   GET /api/system/arq/overview     指定流水线的分片队列总览：在线队列 + 每队列 worker 列表
//   GET /api/system/arq/split-config 指定流水线的切片数配置
//   PUT /api/system/arq/split-config 修改该流水线的切片数
// 说明：
//   - 两条流水线只靠队列名隔离（workflow_queue:split_{N} / ragflow_queue:split_{N}），
//     Redis 实例可以不同，故三个接口都要带 pipeline，页面切选项即切数据源；
//   - 每个 worker 进程写自己的健康 key（{worker}:{queue}:{host}:{pid}，TTL=11s），
//     退出自动删除、失联自动过期 → 每个队列卡片下的 worker 表格即该队列在线节点；
//   - 概览数据 = 分片队列待执行数（队列 ZSET 深度）
//     + 该队列在线 worker（worker 健康 key 的 j_complete/j_failed/j_retried/j_ongoing/queued）；
//     worker 全挂但队列有堆积时，卡片仍会出现（只扫健康 key 会把堆积显示成空白）；
//   - 点队列名可展开/折叠该队列的 worker 表格；
//   - 切片数修改成功后，需按新切片数启动对应流水线的 arq worker 才会消费新队列；
//   - 自动刷新默认关闭，可手动打开（5s 轮询，跟随当前选中的流水线）。

const REFRESH_INTERVAL = 5000; // 自动刷新周期（毫秒）
const MIN_SPLIT = 1; // 与后端 MAX_SPLIT_NUMBER 对齐
const MAX_SPLIT = 32;

// 下拉选项的兜底值：/arq/pipelines 请求失败时仍能让页面可用
const PIPELINE_FALLBACK = [
  { key: 'workflow', label: '工作流执行监控', queueName: 'workflow_queue' },
  { key: 'ragflow', label: '知识库解析监控', queueName: 'ragflow_queue' },
];

const pipelines =
  ref<{ key: string; label: string; queueName?: string }[]>(PIPELINE_FALLBACK);
const pipeline = ref('workflow'); // 当前监控的流水线
const overview = ref<Record<string, any>>({});
const splitNumber = ref(1); // 当前生效的切片数
const editSplit = ref(1); // 输入框中的切片数
const loading = ref(false);
const autoRefresh = ref(false);
const savingSplit = ref(false);
const collapsedQueues = ref<Set<string>>(new Set()); // 已折叠的队列名

let timer: null | ReturnType<typeof setInterval> = null;

const pipelineOptions = computed(() =>
  pipelines.value.map((p) => ({ label: p.label, value: p.key })),
);

/** 当前流水线的基础队列名（切片数配置 key = {队列名}:split_number） */
const splitKeyHint = computed(() => {
  const queueName = pipelines.value.find(
    (p) => p.key === pipeline.value,
  )?.queueName;
  return queueName ? `${queueName}:split_number` : '本流水线的 split_number';
});

/** 拉一次可监控的流水线清单（失败就留着兜底选项，不打断监控） */
async function loadPipelines() {
  try {
    const list = await defHttp.get('/api/system/arq/pipelines');
    if (Array.isArray(list) && list.length > 0) {
      pipelines.value = list;
    }
  } catch {
    // 请求失败由全局拦截器统一提示
  }
}

/** 拉取一次分片队列总览 + 当前切片数（两接口并行，均按选中的流水线） */
async function refresh() {
  try {
    const [ov, cfg] = await Promise.all([
      defHttp.get(`/api/system/arq/overview?pipeline=${pipeline.value}`),
      defHttp.get(`/api/system/arq/split-config?pipeline=${pipeline.value}`),
    ]);
    overview.value = ov ?? {};
    splitNumber.value = cfg?.splitNumber ?? 1;
    editSplit.value = splitNumber.value;
  } catch {
    // 请求失败由全局拦截器统一提示，页面保留上一次数据
  }
}

// 切监控对象：先清掉上一条流水线的残留数据再重拉，避免页面短暂显示别的队列
watch(pipeline, async () => {
  overview.value = {};
  splitNumber.value = 1;
  editSplit.value = 1;
  collapsedQueues.value = new Set();
  await refresh();
});

/** 手动刷新（带 loading） */
async function handleRefresh() {
  loading.value = true;
  try {
    await refresh();
  } finally {
    loading.value = false;
  }
}

/** 自动刷新开关（antd Switch change 参数为 CheckedType，取真值判断） */
function toggleAutoRefresh(checked: boolean | number | string) {
  const on = checked === true || checked === 'true' || checked === 1;
  autoRefresh.value = on;
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (on) {
    timer = setInterval(refresh, REFRESH_INTERVAL);
  }
}

/** 保存当前流水线的切片数：写入其 arq Redis，后续投递按新切片数轮询 */
async function handleSaveSplit() {
  const n = Number(editSplit.value);
  if (!Number.isInteger(n) || n < MIN_SPLIT || n > MAX_SPLIT) {
    message.warning(`切片数需为 ${MIN_SPLIT}~${MAX_SPLIT} 的整数`);
    return;
  }
  savingSplit.value = true;
  try {
    await defHttp.put(
      `/api/system/arq/split-config?pipeline=${pipeline.value}`,
      { splitNumber: n },
    );
    message.success(`切片数已更新为 ${n}`);
    await refresh();
  } finally {
    savingSplit.value = false;
  }
}

onMounted(() => {
  loadPipelines();
  refresh();
});

onUnmounted(() => {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
});

// ==================== 展示辅助 ====================

/** 数字指标格式化：-1 / undefined 表示 Redis 不可达或未获取到 */
function num(v: any, unit = ''): string {
  const n = Number(v);
  if (Number.isNaN(n) || n < 0) {
    return '-';
  }
  return `${n}${unit}`;
}

/** 队列健康判定：存在在线 worker（overview 只返回在线节点） */
function queueHealthy(q: Record<string, any>): boolean {
  return Number(q?.workerCount) > 0;
}

/** 队列卡片列表（overview.queues，按切片号排序） */
function queueList(): Record<string, any>[] {
  return Array.isArray(overview.value?.queues) ? overview.value.queues : [];
}

/** 队列是否处于折叠状态 */
function isCollapsed(queueName: string): boolean {
  return collapsedQueues.value.has(queueName);
}

/** 折叠箭头的样式类（展开时旋转 90°；合成一个绑定避免模板里双 class 换行） */
function arrowClass(queueName: string): string {
  const base = 'inline-block text-xs text-gray-400 transition-transform';
  return isCollapsed(queueName) ? base : `${base} rotate-90`;
}

/** 点队列名展开/折叠 */
function toggleQueue(queueName: string) {
  const next = new Set(collapsedQueues.value);
  if (next.has(queueName)) {
    next.delete(queueName);
  } else {
    next.add(queueName);
  }
  collapsedQueues.value = next;
}

/** worker 表格列定义（消费情况来自健康 key 值） */
const workerColumns = [
  {
    title: 'Worker 节点',
    dataIndex: 'workerId',
    key: 'workerId',
    ellipsis: true,
  },
  { title: '状态', key: 'alive', width: 90, align: 'center' },
  { title: '心跳时间', key: 'heartbeat', width: 170 },
  { title: 'TTL(s)', key: 'healthKeyTtl', width: 80, align: 'center' },
  { title: '累计完成', key: 'complete', width: 100, align: 'right' },
  { title: '累计失败', key: 'failed', width: 100, align: 'right' },
  { title: '累计重试', key: 'retried', width: 100, align: 'right' },
  { title: '执行中', key: 'ongoing', width: 80, align: 'right' },
  { title: '队列待执行', key: 'queued', width: 100, align: 'right' },
];
</script>

<template>
  <Page content-class="arq-monitor-page" :auto-content-height="true">
    <!-- 工具栏：流水线选择 / 切片数配置 / 自动刷新 / 手动刷新 -->
    <Card class="monitor-toolbar" :bordered="false">
      <div class="toolbar-layout">
        <div class="pipeline-panel">
          <div class="monitor-heading">
            <span class="monitor-title">ARQ 分片队列监控</span>
            <span v-if="overview.redis" class="redis-address">
              <span class="redis-dot"></span>
              {{ overview.redis }}
            </span>
          </div>
          <div class="pipeline-selector">
            <span class="control-label">监控对象</span>
            <!-- 工作流执行 worker 与知识库解析 worker 使用各自的数据源 -->
            <Select
              v-model:value="pipeline"
              :options="pipelineOptions"
              style="width: 200px"
            />
          </div>
        </div>

        <div class="toolbar-controls">
          <div class="split-config">
            <span class="control-label">当前切片</span>
            <span class="split-value">{{ splitNumber || '-' }}</span>
            <span class="control-label">调整为</span>
            <InputNumber
              v-model:value="editSplit"
              :min="MIN_SPLIT"
              :max="MAX_SPLIT"
              :step="1"
              style="width: 76px"
            />
            <a-tooltip
              :title="`写入 arq Redis（${splitKeyHint}），该流水线投递将按新切片数轮询；需同步启动对应切片号的 worker`"
            >
              <a-button
                type="primary"
                :loading="savingSplit"
                :disabled="editSplit === splitNumber"
                @click="handleSaveSplit"
              >
                保存配置
              </a-button>
            </a-tooltip>
          </div>

          <div class="refresh-config">
            <span class="control-label">自动刷新</span>
            <Switch
              v-model:checked="autoRefresh"
              checked-children="开"
              un-checked-children="关"
              size="small"
              @change="toggleAutoRefresh"
            />
            <span class="refresh-interval">{{ REFRESH_INTERVAL / 1000 }}s</span>
            <a-button :loading="loading" @click="handleRefresh">刷新</a-button>
          </div>
        </div>
      </div>
    </Card>

    <!-- 每个切片队列一张卡片：点队列名展开/折叠 worker 消费情况 -->
    <Card
      v-for="q in queueList()"
      :key="q.queueName"
      class="queue-card"
      :bordered="false"
    >
      <template #title>
        <div
          class="queue-heading"
          tabindex="0"
          @click="toggleQueue(q.queueName)"
          @keydown.enter="toggleQueue(q.queueName)"
        >
          <span class="queue-arrow" :class="arrowClass(q.queueName)">▸</span>
          <span class="queue-name">{{ q.queueName }}</span>
          <span
            class="queue-status"
            :class="queueHealthy(q) ? 'is-online' : 'is-offline'"
          >
            <span class="status-dot"></span>
            {{ queueHealthy(q) ? '队列在线' : '无可用 Worker' }}
          </span>
        </div>
      </template>
      <template #extra>
        <div class="queue-metrics">
          <span class="queue-metric">
            <span class="metric-label">在线 Worker</span>
            <strong>{{ num(q.workerCount) }}</strong>
          </span>
          <span class="metric-divider"></span>
          <span class="queue-metric">
            <span class="metric-label">待执行</span>
            <strong :class="{ 'has-backlog': Number(q.depth) > 0 }">
              {{ num(q.depth) }}
            </strong>
          </span>
        </div>
      </template>

      <div v-show="!isCollapsed(q.queueName)" class="worker-section">
        <div class="worker-section__header">
          <span class="worker-section__title">Worker 消费情况</span>
          <span class="worker-section__caption">
            节点退出后自动移除，失联节点将在 TTL 过期后消失
          </span>
        </div>
        <a-table
          class="worker-table"
          :data-source="q.workers ?? []"
          :columns="workerColumns"
          :pagination="false"
          size="small"
          row-key="workerId"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'alive'">
              <a-tag :color="record.alive ? 'success' : 'error'">
                {{ record.alive ? '在线' : '失联' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'heartbeat'">
              {{ record.heartbeat || '-' }}
            </template>
            <template v-else-if="column.key === 'healthKeyTtl'">
              {{ num(record.healthKeyTtl) }}
            </template>
            <template
              v-else-if="
                ['complete', 'failed', 'retried', 'ongoing', 'queued'].includes(
                  column.key,
                )
              "
            >
              {{ num(record[column.key]) }}
            </template>
          </template>
          <template #emptyText>
            <span class="worker-empty">
              暂无 Worker 节点，请检查对应切片号的 Worker 或 Redis 连接
            </span>
          </template>
        </a-table>
      </div>
    </Card>

    <!-- 空态：Redis 不可达 / 该流水线未配 / 尚无任何队列 -->
    <div v-if="queueList().length === 0" class="monitor-empty">
      <a-empty
        :description="
          overview.error ||
          '暂无队列数据（检查该流水线的 arq Redis 连接与 worker 启动状态）'
        "
      />
    </div>
  </Page>
</template>

<style lang="less" scoped>
:deep(.arq-monitor-page) {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}

.monitor-toolbar,
.queue-card,
.monitor-empty {
  overflow: hidden;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color, #e6ebf0);
  border-radius: 10px;
  box-shadow: 0 2px 10px rgb(15 23 42 / 3%);
}

.monitor-toolbar {
  :deep(.ant-card-body) {
    padding: 16px 18px;
  }
}

.toolbar-layout,
.toolbar-controls,
.split-config,
.refresh-config,
.monitor-heading,
.pipeline-selector {
  display: flex;
  align-items: center;
}

.toolbar-layout {
  gap: 24px;
  justify-content: space-between;
}

.pipeline-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 250px;
}

.monitor-heading {
  flex-wrap: wrap;
  gap: 8px 12px;
}

.monitor-title {
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
  color: var(--text-color, #24292f);
}

.redis-address {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 2px 7px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  line-height: 18px;
  color: var(--text-color-secondary, #737d89);
  background: var(--component-background-light, #f5f7fa);
  border-radius: 4px;
}

.redis-dot,
.status-dot {
  display: inline-block;
  flex: none;
  width: 6px;
  height: 6px;
  background: #52c41a;
  border-radius: 50%;
}

.pipeline-selector {
  gap: 10px;
}

.control-label {
  flex: none;
  font-size: 12px;
  color: var(--text-color-secondary, #7a8491);
}

.toolbar-controls {
  flex-wrap: wrap;
  gap: 12px 18px;
  justify-content: flex-end;
}

.split-config,
.refresh-config {
  gap: 9px;
}

.split-config {
  padding-right: 18px;
  border-right: 1px solid var(--border-color, #e8edf2);
}

.split-value {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  height: 28px;
  padding: 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border-radius: 6px;
}

.refresh-interval {
  margin-left: -5px;
  font-size: 12px;
  color: var(--text-color-secondary, #8a939e);
}

.queue-card {
  :deep(.ant-card-head) {
    min-height: 58px;
    padding: 0 18px;
    background: var(--component-background-light, #fafbfc);
    border-bottom-color: var(--border-color, #e9edf2);
  }

  :deep(.ant-card-head-title),
  :deep(.ant-card-extra) {
    padding: 12px 0;
  }

  :deep(.ant-card-body) {
    padding: 16px 18px 18px;
  }
}

.queue-heading {
  display: flex;
  gap: 9px;
  align-items: center;
  min-width: 0;
  cursor: pointer;
  outline: none;
  user-select: none;

  &:focus-visible {
    border-radius: 4px;
    box-shadow: 0 0 0 2px hsl(var(--primary) / 20%);
  }
}

.queue-arrow {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  color: hsl(var(--primary)) !important;
}

.queue-name {
  overflow: hidden;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-color, #2f3742);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-status {
  display: inline-flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 12px;

  &.is-online {
    color: #389e0d;
    background: rgb(82 196 26 / 9%);
  }

  &.is-offline {
    color: #cf1322;
    background: rgb(255 77 79 / 8%);

    .status-dot {
      background: #ff4d4f;
    }
  }
}

.queue-metrics {
  display: flex;
  gap: 16px;
  align-items: center;
}

.queue-metric {
  display: inline-flex;
  gap: 8px;
  align-items: baseline;
  font-size: 13px;

  strong {
    min-width: 18px;
    font-size: 16px;
    font-weight: 600;
    color: var(--text-color, #303642);
    text-align: right;
    font-variant-numeric: tabular-nums;

    &.has-backlog {
      color: #d48806;
    }
  }
}

.metric-label {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-color-secondary, #8a939e);
}

.metric-divider {
  width: 1px;
  height: 16px;
  background: var(--border-color, #e2e7ed);
}

.worker-section__header {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: baseline;
  margin-bottom: 12px;
}

.worker-section__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color, #343b46);
}

.worker-section__caption,
.worker-empty {
  font-size: 12px;
  color: var(--text-color-secondary, #8a939e);
}

.worker-table {
  overflow: hidden;
  border: 1px solid var(--border-color, #e7ecf1);
  border-radius: 8px;

  :deep(.ant-table) {
    font-size: 13px;
  }

  :deep(.ant-table-thead > tr > th) {
    padding: 10px 12px;
    font-weight: 600;
    color: #525c69;
    background: #f7f9fb;
    border-bottom-color: var(--border-color, #e5eaf0);
  }

  :deep(.ant-table-tbody > tr > td) {
    padding: 11px 12px;
    font-variant-numeric: tabular-nums;
  }

  :deep(.ant-table-tbody > tr:last-child > td) {
    border-bottom: 0;
  }
}

.monitor-empty {
  padding: 36px 16px;
}

@media (max-width: 1280px) {
  .toolbar-layout {
    align-items: flex-start;
  }

  .toolbar-controls {
    gap: 10px;
  }

  .split-config {
    padding-right: 10px;
  }
}

@media (max-width: 960px) {
  .toolbar-layout {
    flex-direction: column;
  }

  .pipeline-panel,
  .toolbar-controls {
    width: 100%;
  }

  .toolbar-controls {
    justify-content: space-between;
  }
}

@media (max-width: 640px) {
  .split-config {
    flex-wrap: wrap;
    padding-right: 0;
    border-right: 0;
  }

  .worker-section__caption {
    width: 100%;
  }
}
</style>
