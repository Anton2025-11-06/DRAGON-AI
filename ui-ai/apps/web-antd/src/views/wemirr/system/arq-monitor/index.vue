<script lang="ts" setup name="ArqMonitorPage">
import { onMounted, onUnmounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Card } from 'ant-design-vue';

import { defHttp } from '#/api/request';

// ==================== ARQ 任务队列监控（需求 6） ====================
// 数据源：
//   GET /api/system/arq/queue-metrics  队列深度/已到点/延迟/执行中/健康状态
//   GET /api/system/arq/worker-health   worker 节点清单 + 聚合统计
// 说明：
//   - 每个 worker 进程写自己的节点级健康 key（{queue}:workers:{hostname}:{pid}，
//     TTL=11s），退出自动删除、失联自动过期 → 节点表格可枚举所有存活 worker；
//   - 聚合统计为全部在线节点求和（queued 为整队列深度，取最新节点避免重复计数）；
//   - Redis 不可达时各指标返回 -1，页面统一显示为 '-'；
//   - 自动刷新默认关闭，可手动打开（5s 轮询），避免监控页常驻轮询打扰后端。

const REFRESH_INTERVAL = 5000; // 自动刷新周期（毫秒）

const queueMetrics = ref<Record<string, any>>({});
const workerHealth = ref<Record<string, any>>({});
const loading = ref(false);
const autoRefresh = ref(false);

let timer: null | ReturnType<typeof setInterval> = null;

/** 拉取一次队列指标 + worker 健康（两接口并行） */
async function refresh() {
  try {
    const [q, w] = await Promise.all([
      defHttp.get('/api/system/arq/queue-metrics'),
      defHttp.get('/api/system/arq/worker-health'),
    ]);
    queueMetrics.value = q ?? {};
    workerHealth.value = w ?? {};
  } catch {
    // 请求失败由全局拦截器统一提示，页面保留上一次数据
  }
}

/** 手动刷新（带 loading） */
async function handleRefresh() {
  loading.value = true;
  try {
    await refresh();
  } finally {
    loading.value = false;
  }
}

/** 自动刷新开关 */
function toggleAutoRefresh(checked: boolean) {
  autoRefresh.value = checked;
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (checked) {
    timer = setInterval(refresh, REFRESH_INTERVAL);
  }
}

onMounted(() => {
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

/** 队列健康判定：Redis 可达且存在新鲜 worker 心跳 */
function queueHealthy(): boolean {
  return Boolean(queueMetrics.value?.healthy);
}

/** worker 在线判定：存在心跳新鲜的节点 */
function workerAlive(): boolean {
  return Boolean(workerHealth.value?.alive);
}

/** 节点清单（worker-health.workers 数组，分布式扩展后每行一个节点） */
function workerRows(): Record<string, any>[] {
  return Array.isArray(workerHealth.value?.workers)
    ? workerHealth.value.workers
    : [];
}

/** 节点表格列定义 */
const workerColumns = [
  {
    title: 'Worker 节点',
    dataIndex: 'workerId',
    key: 'workerId',
    ellipsis: true,
  },
  { title: '状态', key: 'alive', width: 90, align: 'center' },
  { title: '心跳时间', key: 'heartbeat', width: 170 },
  { title: 'TTL(s)', key: 'ttl', width: 80, align: 'center' },
  { title: '累计完成', key: 'complete', width: 100, align: 'right' },
  { title: '累计失败', key: 'failed', width: 100, align: 'right' },
  { title: '执行中', key: 'ongoing', width: 80, align: 'right' },
];

/** 心跳时间（队列接口与 worker 接口都返回，取任一个） */
function heartbeatText(): string {
  return queueMetrics.value?.heartbeat ?? workerHealth.value?.heartbeat ?? '-';
}
</script>

<template>
  <Page content-class="flex flex-col gap-2" :auto-content-height="true">
    <!-- 工具栏：队列名 / 健康状态 / Redis 地址 / 自动刷新开关 / 手动刷新 -->
    <Card class="w-full">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="font-medium">
            ARQ 任务队列
            <a-tag color="blue">{{ queueMetrics.queueName || '-' }}</a-tag>
          </span>
          <a-tag :color="queueHealthy() ? 'success' : 'error'">
            {{ queueHealthy() ? '队列健康' : '异常' }}
          </a-tag>
          <span class="text-xs text-gray-500">{{
            queueMetrics.redis || ''
          }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-500">
            自动刷新 {{ REFRESH_INTERVAL / 1000 }}s
          </span>
          <a-switch
            :checked="autoRefresh"
            checked-children="开"
            un-checked-children="关"
            @change="toggleAutoRefresh"
          />
          <a-button type="primary" :loading="loading" @click="handleRefresh">
            刷新
          </a-button>
        </div>
      </div>
    </Card>

    <!-- 队列指标卡片 -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <Card class="w-full">
        <a-statistic
          title="队列深度（待执行任务）"
          :value="Number(num(queueMetrics.depth))"
        />
      </Card>
      <Card class="w-full">
        <a-statistic
          title="已到点待执行"
          :value="Number(num(queueMetrics.due))"
        />
      </Card>
      <Card class="w-full">
        <a-statistic
          title="延迟任务（未到执行时间）"
          :value="Number(num(queueMetrics.delayed))"
        />
      </Card>
      <Card class="w-full">
        <a-statistic
          title="执行中任务（近似）"
          :value="Number(num(queueMetrics.inProgress))"
        />
      </Card>
    </div>

    <!-- worker 节点健康：聚合概览 + 节点清单 -->
    <Card class="w-full" title="arq Worker 进程健康">
      <template #extra>
        <a-tag :color="workerAlive() ? 'success' : 'error'">
          {{ workerAlive() ? '在线' : '离线/失联' }}
        </a-tag>
        <a-tag v-if="workerHealth.count !== undefined" color="blue">
          在线 {{ workerHealth.count ?? 0 }} 节点
        </a-tag>
      </template>
      <a-descriptions :column="3" bordered size="small">
        <a-descriptions-item label="心跳时间（最新）">
          {{ heartbeatText() }}
        </a-descriptions-item>
        <a-descriptions-item label="距上次心跳（秒）">
          {{ num(workerHealth.secondsSinceHeartbeat) }}
        </a-descriptions-item>
        <a-descriptions-item label="健康 key 剩余 TTL（秒）">
          {{ num(workerHealth.healthKeyTtl) }}
        </a-descriptions-item>
        <a-descriptions-item label="累计完成（聚合）">
          {{ num(workerHealth.complete) }}
        </a-descriptions-item>
        <a-descriptions-item label="累计失败（聚合）">
          {{ num(workerHealth.failed) }}
        </a-descriptions-item>
        <a-descriptions-item label="累计重试（聚合）">
          {{ num(workerHealth.retried) }}
        </a-descriptions-item>
        <a-descriptions-item label="当前执行中（聚合）">
          {{ num(workerHealth.ongoing) }}
        </a-descriptions-item>
        <a-descriptions-item label="队列待执行（worker 视角）">
          {{ num(workerHealth.queued) }}
        </a-descriptions-item>
        <a-descriptions-item label="失联原因">
          {{ workerHealth.reason || '-' }}
        </a-descriptions-item>
      </a-descriptions>
    </Card>

    <!-- worker 节点清单（分布式扩展后每行一个节点，hostname:pid） -->
    <Card class="w-full" title="Worker 节点清单">
      <template #extra>
        <span class="text-xs text-gray-500">
          失联（kill -9）节点在 TTL 过期后自动消失
        </span>
      </template>
      <a-table
        :data-source="workerRows()"
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
          <template v-else-if="column.key === 'ttl'">
            {{ num(record.healthKeyTtl) }}
          </template>
          <template v-else-if="column.key === 'complete'">
            {{ num(record.complete) }}
          </template>
          <template v-else-if="column.key === 'failed'">
            {{ num(record.failed) }}
          </template>
          <template v-else-if="column.key === 'ongoing'">
            {{ num(record.ongoing) }}
          </template>
        </template>
        <template #emptyText>
          <span class="text-xs text-gray-500">
            暂无节点（worker 未启动或 Redis 不可达）
          </span>
        </template>
      </a-table>
    </Card>
  </Page>
</template>

<style lang="less" scoped>
:deep(.ant-card-body) {
  padding: 12px;
}
</style>
