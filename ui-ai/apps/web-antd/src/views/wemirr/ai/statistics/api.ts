import { defHttp } from '#/api/request';

const BASE_URL = '/api/workflow/statistics';

export type StatisticsPeriod = 'DAY' | 'MONTH' | 'WEEK';
export type StatisticsNumericValue = number | string;

export interface AiStatisticsSummaryResp {
  activeUserCount: StatisticsNumericValue;
  conversationCount: StatisticsNumericValue;
  inputTokens: StatisticsNumericValue;
  outputTokens: StatisticsNumericValue;
  totalTokens: StatisticsNumericValue;
}

export interface AiStatisticsRankingResp {
  conversationCount: StatisticsNumericValue;
  inputTokens: StatisticsNumericValue;
  outputTokens: StatisticsNumericValue;
  rank: number;
  totalTokens: StatisticsNumericValue;
  userId: string;
  userName?: string;
}

export interface AiStatisticsDashboardResp {
  conversationRanking: AiStatisticsRankingResp[];
  period: StatisticsPeriod;
  periodEnd: string;
  periodStart: string;
  summary: AiStatisticsSummaryResp;
  tokenRanking: AiStatisticsRankingResp[];
}

export const getAiStatisticsDashboard = (
  period: StatisticsPeriod,
  limit = 10,
) =>
  defHttp.get<AiStatisticsDashboardResp>(`${BASE_URL}/dashboard`, {
    params: { limit, period },
  });
