/**
 * RAG 知识评测（RAGAS）API 客户端（对齐 service/service_rag/routers/eval_router.py）。
 *
 * 口径：
 * - 端点走网关前缀 /api/rag/eval/**（defHttp 的 baseURL 已剥掉 /api，见 #/api/request）；
 * - 出入参一律 camelCase（后端 rag_schema.dump() 只转字段名；metricAvgs 是普通 dict，
 *   其键保持后端原样 = ragas 列名 faithfulness/answer_relevancy/... 不转驼峰，前端按此取值）；
 * - 枚举/标签集中在此定义（与 common_constants/rag_constant.py 第十一节静态对账）。
 *
 * 模型下拉复用 ../doc/api：那份是 RAG 模块契约的唯一来源（fetchChatModels 取 text_to_text，
 * 相似度向量取 MT_TEXT_EMBEDDING），拆两份只会两边各漂一半。
 */
import { MT_TEXT_EMBEDDING } from '#/api/ai-workflow/const';
import { defHttp } from '#/api/request';

import { fetchChatModels, type KbOption } from '../doc/api';

const BASE = '/api/rag';
const EVAL_BASE = `${BASE}/eval`;
const WORKFLOW_BASE = '/api/workflow';

// ==================== 枚举（与 rag_constant.py 第十一节一一对应） ====================

/** 运行状态 */
export type EvalRunStatus = 'DONE' | 'FAILED' | 'PENDING' | 'RUNNING';
export const EVAL_STATUS_ALL: EvalRunStatus[] = [
  'PENDING',
  'RUNNING',
  'DONE',
  'FAILED',
];
export const EVAL_STATUS_LABELS: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '评测中',
  DONE: '已完成',
  FAILED: '失败',
};
export const EVAL_STATUS_COLORS: Record<string, string> = {
  PENDING: 'default',
  RUNNING: 'processing',
  DONE: 'success',
  FAILED: 'error',
};

/** 条项状态 */
export const EVAL_ITEM_LABELS: Record<string, string> = {
  PENDING: '待评测',
  DONE: '已完成',
  FAILED: '失败',
};

/**
 * 五项指标：键 = ragas 结果列名（后端 metricAvgs 的键、逐 item 的字段名都以此为准），
 * label 给页面展示。与 rag_constant.EVAL_METRIC_* / EVAL_METRIC_LABELS 同清单。
 */
export const EVAL_METRICS_ALL = [
  'faithfulness',
  'answer_relevancy',
  'context_precision',
  'context_recall',
  'answer_correctness',
] as const;
export type EvalMetricKey = (typeof EVAL_METRICS_ALL)[number];
export const EVAL_METRIC_LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  answer_relevancy: '答案相关性',
  context_precision: '上下文精确率',
  context_recall: '上下文召回率',
  answer_correctness: '答案正确性',
};

// ==================== 类型 ====================

export interface EvalPair {
  question: string;
  reference?: string | null;
}

export interface EvalRunCreateReq {
  kbId: number;
  name?: string;
  generationModelId: number;
  judgeModelId: number;
  embedModelId: number;
  pairs: EvalPair[];
  topK?: number;
  scoreThreshold?: number;
  retrievalMode?: string;
  withGraph?: boolean;
  graphSourceChunks?: boolean;
}

export interface EvalRunPageReq {
  current: number;
  size: number;
  kbId?: number;
  status?: string;
}

export interface EvalContext {
  content?: string;
  score?: number;
  recall?: string;
  docName?: string;
  [k: string]: any;
}

export interface EvalItemResp {
  itemId: number;
  runId: number;
  question: string;
  reference?: null | string;
  generatedAnswer?: null | string;
  contexts: EvalContext[];
  faithfulness?: null | number;
  answerRelevancy?: null | number;
  contextPrecision?: null | number;
  contextRecall?: null | number;
  answerCorrectness?: null | number;
  tookRecallMs: number;
  tookGenerateMs: number;
  tookScoreMs: number;
  status: string;
  statusLabel?: string;
  error?: null | string;
}

export interface EvalRunResp {
  runId: number;
  kbId: number;
  kbName?: string;
  name?: string;
  generationModelId: number;
  judgeModelId: number;
  embedModelId: number;
  topK: number;
  scoreThreshold: number;
  retrievalMode?: string;
  withGraph: boolean;
  graphSourceChunks: boolean;
  totalPairs: number;
  donePairs: number;
  status: string;
  statusLabel?: string;
  avgLatencyMs: number;
  error?: string;
  createdBy: number;
  createTime?: string;
  updateTime?: string;
  /** 五项指标均值（键 = ragas 列名，非驼峰；无数据的指标为 null） */
  metricAvgs: Record<string, null | number>;
  items?: EvalItemResp[];
}

export interface PageResp<T> {
  records: T[];
  total: number;
  current: number;
  size: number;
}

// ==================== 接口 ====================

/** 可评测的知识库列表（按 eval ACL 过滤） */
export const EvalKbs = () => defHttp.get<KbOption[]>(`${EVAL_BASE}/kbs`);

/** 发起评测：落库后投队列，立即返回 runId（不同步跑评测） */
export const CreateEvalRun = (data: EvalRunCreateReq) =>
  defHttp.post<{ runId: number; totalPairs: number }>(
    `${EVAL_BASE}/runs`,
    data,
  );

/** 运行历史分页 */
export const EvalRunPage = (data: EvalRunPageReq) =>
  defHttp.post<PageResp<EvalRunResp>>(`${EVAL_BASE}/page`, data);

/** 评测详情（run + 逐问答对结果 + 指标均值） */
export const EvalRunDetail = (runId: number) =>
  defHttp.get<EvalRunResp>(`${EVAL_BASE}/runs/${runId}`);

/** 删除评测记录（软删） */
export const DeleteEvalRun = (runId: number) =>
  defHttp.delete<boolean>(`${EVAL_BASE}/runs/${runId}`);

/** 下载问答对模板（走带鉴权头的 blob 下载，不是裸 window.open） */
export const DownloadEvalTemplate = () =>
  defHttp.downloadFile(`${EVAL_BASE}/template`, '问答对模板.xlsx', {
    method: 'GET',
  });

/** 导入问答对 Excel：仅解析回显，不落库 */
export const ImportEvalPairs = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return defHttp.post<{ pairs: EvalPair[]; total: number }>(
    `${EVAL_BASE}/pairs/import`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60_000 },
  );
};

// ==================== 模型下拉（复用 doc/api 与 workflow /models/list） ====================

export interface ModelOption {
  label: string;
  value: number;
}

/** 生成/裁判模型都是 text_to_text 对话模型：直接复用 doc/api 的 fetchChatModels */
export const fetchEvalChatModels = () => fetchChatModels();

/** 相似度向量模型：answer_relevancy 用文本向量（RAGAS embeddings 走文本编码） */
export async function fetchEvalEmbedModels(): Promise<ModelOption[]> {
  const rows = await defHttp.get<
    Array<{ id: number; provider: string; name: string }>
  >(`${WORKFLOW_BASE}/models/list`, { params: { type: MT_TEXT_EMBEDDING } });
  return (rows || []).map((m) => ({ label: `${m.provider} / ${m.name}`, value: m.id }));
}
