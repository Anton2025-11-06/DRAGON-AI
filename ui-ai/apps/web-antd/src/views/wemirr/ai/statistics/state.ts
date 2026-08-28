import { ref } from 'vue';

import type { AiStatisticsDashboardResp, StatisticsPeriod } from './api';

import { getAiStatisticsDashboard } from './api';

type DashboardFetcher = (
  period: StatisticsPeriod,
  limit?: number,
) => Promise<AiStatisticsDashboardResp>;

export function createEmptyDashboard(
  period: StatisticsPeriod = 'DAY',
): AiStatisticsDashboardResp {
  return {
    conversationRanking: [],
    period,
    periodEnd: '',
    periodStart: '',
    summary: {
      activeUserCount: 0,
      conversationCount: 0,
      inputTokens: 0,
      outputTokens: 0,
      totalTokens: 0,
    },
    tokenRanking: [],
  };
}

export function useAiStatisticsDashboard(
  fetchDashboard: DashboardFetcher = getAiStatisticsDashboard,
) {
  const loading = ref(false);
  const error = ref(false);
  const activePeriod = ref<StatisticsPeriod>('DAY');
  const dashboard = ref<AiStatisticsDashboardResp>(createEmptyDashboard());
  let latestRequestId = 0;

  async function loadDashboard() {
    const requestId = ++latestRequestId;
    const requestedPeriod = activePeriod.value;
    loading.value = true;
    error.value = false;

    try {
      const nextDashboard = await fetchDashboard(requestedPeriod, 10);
      if (requestId !== latestRequestId || activePeriod.value !== requestedPeriod) {
        return false;
      }
      dashboard.value = nextDashboard;
      return true;
    } catch (cause) {
      if (requestId !== latestRequestId || activePeriod.value !== requestedPeriod) {
        return false;
      }
      error.value = true;
      throw cause;
    } finally {
      if (requestId === latestRequestId) {
        loading.value = false;
      }
    }
  }

  return {
    activePeriod,
    dashboard,
    error,
    loadDashboard,
    loading,
  };
}
