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
  <Page content-class="flex flex-col gap-2" :auto-content-height="true">
    <!-- 工具栏：流水线选择 / 切片数配置 / 自动刷新 / 手动刷新 -->
    <Card class="w-full">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="font-medium">ARQ 分片队列监控</span>
          <!-- 选监控哪条流水线：工作流执行 worker 与知识库解析 worker 各自的队列 -->
          <Select
            v-model:value="pipeline"
            :options="pipelineOptions"
            size="small"
            style="width: 160px"
          />
          <span class="text-xs text-gray-500">
            {{ overview.redis || '' }}
          </span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-500">当前切片数</span>
          <a-tag color="blue">{{ splitNumber || '-' }}</a-tag>
          <InputNumber
            v-model:value="editSplit"
            :min="MIN_SPLIT"
            :max="MAX_SPLIT"
            :step="1"
            size="small"
            style="width: 90px"
          />
          <a-tooltip
            :title="`写入 arq Redis（${splitKeyHint}），该流水线投递将按新切片数轮询；需同步启动对应切片号的 worker`"
          >
            <a-button
              type="primary"
              size="small"
              :loading="savingSplit"
              :disabled="editSplit === splitNumber"
              @click="handleSaveSplit"
            >
              修改切片数
            </a-button>
          </a-tooltip>
          <span class="text-xs text-gray-500">
            自动刷新 {{ REFRESH_INTERVAL / 1000 }}s
          </span>
          <Switch
            v-model:checked="autoRefresh"
            checked-children="开"
            un-checked-children="关"
            size="small"
            @change="toggleAutoRefresh"
          />
          <a-button type="primary" :loading="loading" @click="handleRefresh">
            刷新
          </a-button>
        </div>
      </div>
    </Card>

    <!-- 每个切片队列一张卡片：点队列名展开/折叠 worker 消费情况 -->
    <Card v-for="q in queueList()" :key="q.queueName" class="w-full">
      <template #title>
        <div
          class="flex cursor-pointer select-none flex-wrap items-center gap-2 text-base"
          @click="toggleQueue(q.queueName)"
        >
          <span :class="arrowClass(q.queueName)">▸</span>
          <span class="font-medium">队列 {{ q.queueName }}</span>
          <a-tag
            class="status-tag"
            :color="queueHealthy(q) ? 'success' : 'error'"
          >
            {{ queueHealthy(q) ? '队列在线' : '无可用 worker' }}
          </a-tag>
          <a-tag class="status-tag" color="blue">
            {{ num(q.workerCount) }} 个在线 worker
          </a-tag>
          <!-- 队列待执行数：来自队列 ZSET 深度，worker 全挂时也能看到堆积 -->
          <a-tag
            class="status-tag"
            :color="Number(q.depth) > 0 ? 'warning' : 'default'"
          >
            {{ num(q.depth) }} 条待执行
          </a-tag>
        </div>
      </template>

      <div v-show="!isCollapsed(q.queueName)">
        <div class="mb-1 mt-3 flex items-center gap-2">
          <span class="text-sm font-medium">Worker 消费情况</span>
          <span class="text-xs text-gray-500">
            每个 worker 一个健康 key，退出自动删除、失联 TTL 过期后消失
          </span>
        </div>
        <a-table
          :data-source="q.workers ?? []"
          :columns="workerColumns"
          :pagination="false"
          size="small"
          bordered
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
            <span class="text-xs text-gray-500">
              该队列暂无 worker 节点（未启动对应切片号的 worker 或 Redis
              不可达）
            </span>
          </template>
        </a-table>
      </div>
    </Card>

    <!-- 空态：Redis 不可达 / 该流水线未配 / 尚无任何队列 -->
    <a-empty
      v-if="queueList().length === 0"
      class="mt-8"
      :description="
        overview.error ||
        '暂无队列数据（检查该流水线的 arq Redis 连接与 worker 启动状态）'
      "
    />
  </Page>
</template>

<style lang="less" scoped>
:deep(.ant-card-body) {
  padding: 12px;
}

/* 队列状态 tag 比默认(12px)大一号,与标题左对齐展示 */
:deep(.status-tag) {
  height: 24px;
  padding-inline: 10px;
  font-size: 14px;
  line-height: 22px;
}
</style>
