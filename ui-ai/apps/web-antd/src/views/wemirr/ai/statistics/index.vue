<script lang="ts" setup name="AiStatisticsPage">
import type { StatisticsPeriod } from './api';

import { computed, onMounted } from 'vue';

import {
  AnalysisChartCard,
  AnalysisOverview,
} from '@vben/common-ui';

import {
  BarChartOutlined,
  MessageOutlined,
  ReloadOutlined,
  TrophyOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { buildOverviewItems, formatNumber } from './format';
import { useAiStatisticsDashboard } from './state';

const {
  activePeriod,
  dashboard,
  error,
  loadDashboard: loadDashboardState,
  loading,
} = useAiStatisticsDashboard();

const periodOptions: Array<{ label: string; value: StatisticsPeriod }> = [
  { label: '今日', value: 'DAY' },
  { label: '本周', value: 'WEEK' },
  { label: '本月', value: 'MONTH' },
];

const overviewItems = computed(() =>
  buildOverviewItems(dashboard.value.summary),
);

const periodRangeText = computed(() => {
  const start = formatDateTime(dashboard.value.periodStart);
  const end = formatDateTime(dashboard.value.periodEnd);
  return start && end ? `${start} 至 ${end}` : '-';
});

async function loadDashboard() {
  try {
    await loadDashboardState();
  } catch {
    message.error('统计数据加载失败');
  }
}

function formatDateTime(value?: string) {
  if (!value) {
    return '';
  }
  return new Date(value).toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
  });
}

function resolveUserName(userName: undefined | string, userId: string) {
  return userName || `用户 ${userId}`;
}

onMounted(loadDashboard);
</script>

<template>
  <div class="ai-statistics-page">
    <div class="page-toolbar">
      <div class="page-title">
        <BarChartOutlined />
        <span>AI 使用统计</span>
        <a-tag color="blue">{{ periodRangeText }}</a-tag>
      </div>
      <a-space>
        <a-segmented
          v-model:value="activePeriod"
          :options="periodOptions"
          @change="loadDashboard"
        />
        <a-button :loading="loading" @click="loadDashboard">
          <template #icon><ReloadOutlined /></template>
        </a-button>
      </a-space>
    </div>

    <a-spin :spinning="loading">
      <a-alert
        v-if="error"
        class="statistics-error"
        message="统计数据加载失败，请重试"
        show-icon
        type="error"
      >
        <template #action>
          <a-button size="small" @click="loadDashboard">重试</a-button>
        </template>
      </a-alert>

      <template v-else>
        <AnalysisOverview :items="overviewItems" />

        <div class="ranking-grid">
          <AnalysisChartCard title="Token 消耗排行榜">
            <div class="ranking-card-heading">
              <TrophyOutlined />
              <span>按输入/输出 Token 合计排序</span>
            </div>
            <div class="ranking-list">
              <div
                v-for="item in dashboard.tokenRanking"
                :key="`token-${item.userId}`"
                class="ranking-item"
              >
                <span class="rank-badge">{{ item.rank }}</span>
                <div class="rank-user">
                  <span class="user-name">
                    {{ resolveUserName(item.userName, item.userId) }}
                  </span>
                  <span class="user-meta">
                    输入 {{ formatNumber(item.inputTokens) }} / 输出
                    {{ formatNumber(item.outputTokens) }}
                  </span>
                </div>
                <span class="rank-value">
                  {{ formatNumber(item.totalTokens) }}
                </span>
              </div>
              <a-empty
                v-if="dashboard.tokenRanking.length === 0"
                description="暂无数据"
              />
            </div>
          </AnalysisChartCard>

          <AnalysisChartCard title="对话次数排行榜">
            <div class="ranking-card-heading">
              <MessageOutlined />
              <span>按用户提问次数排序</span>
            </div>
            <div class="ranking-list">
              <div
                v-for="item in dashboard.conversationRanking"
                :key="`conversation-${item.userId}`"
                class="ranking-item"
              >
                <span class="rank-badge">{{ item.rank }}</span>
                <div class="rank-user">
                  <span class="user-name">
                    {{ resolveUserName(item.userName, item.userId) }}
                  </span>
                  <span class="user-meta">
                    Token {{ formatNumber(item.totalTokens) }}
                  </span>
                </div>
                <span class="rank-value">
                  {{ formatNumber(item.conversationCount) }}
                </span>
              </div>
              <a-empty
                v-if="dashboard.conversationRanking.length === 0"
                description="暂无数据"
              />
            </div>
          </AnalysisChartCard>
        </div>
      </template>
    </a-spin>
  </div>
</template>

<style lang="less" scoped>
.ai-statistics-page {
  min-height: 100%;
  padding: 20px;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  font-size: 18px;
  font-weight: 600;
}

.ranking-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.statistics-error {
  margin-top: 16px;
}

.ranking-list {
  display: flex;
  flex-direction: column;
  min-height: 260px;
  gap: 8px;
}

.ranking-card-heading {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  color: #595959;
}

.ranking-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
}

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  font-weight: 600;
  color: #1677ff;
  background: #e6f4ff;
  border-radius: 50%;
}

.rank-user {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-name {
  overflow: hidden;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-meta {
  overflow: hidden;
  font-size: 12px;
  color: #8c8c8c;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-value {
  font-weight: 600;
  white-space: nowrap;
}

@media (width >= 1024px) {
  .ranking-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (width < 768px) {
  .page-toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .page-title {
    flex-wrap: wrap;
  }
}
</style>
