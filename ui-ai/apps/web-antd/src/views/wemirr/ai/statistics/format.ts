import type { AnalysisOverviewItem } from '@vben/common-ui';

import type {
  AiStatisticsSummaryResp,
  StatisticsNumericValue,
} from './api';

export function formatNumber(value?: null | StatisticsNumericValue) {
  const safeValue = toDisplayNumber(value);
  if (safeValue >= 100_000_000) {
    return `${trimFixed(safeValue / 100_000_000)} 亿`;
  }
  if (safeValue >= 10_000) {
    return `${trimFixed(safeValue / 10_000)} 万`;
  }
  return safeValue.toLocaleString('zh-CN');
}

export function buildOverviewItems(
  summary: AiStatisticsSummaryResp,
): AnalysisOverviewItem[] {
  const inputTokens = toDisplayNumber(summary.inputTokens);
  const outputTokens = toDisplayNumber(summary.outputTokens);
  const totalTokens = toDisplayNumber(summary.totalTokens);
  const conversationCount = toDisplayNumber(summary.conversationCount);
  const activeUserCount = toDisplayNumber(summary.activeUserCount);

  return [
    {
      icon: 'mingcute:input-method-line',
      title: '输入 Token',
      totalTitle: '总输入',
      totalValue: inputTokens,
      value: inputTokens,
    },
    {
      icon: 'mingcute:send-plane-line',
      title: '输出 Token',
      totalTitle: '总输出',
      totalValue: outputTokens,
      value: outputTokens,
    },
    {
      icon: 'mingcute:chart-bar-line',
      title: '总 Token',
      totalTitle: '本期消耗',
      totalValue: totalTokens,
      value: totalTokens,
    },
    {
      icon: 'mingcute:message-4-line',
      title: '对话次数',
      totalTitle: '活跃用户',
      totalValue: activeUserCount,
      value: conversationCount,
    },
  ];
}

function toDisplayNumber(value?: null | StatisticsNumericValue) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function trimFixed(value: number) {
  return value.toFixed(2).replace(/\.?0+$/, '');
}
