/**
 * RAG 知识库 API（对齐 service/service_rag 新契约，SPEC §11）。
 *
 * 口径：
 * - 所有端点走网关前缀 /api/rag（defHttp 的 baseURL 已剥掉 /api，见 #/api/request）；
 * - 出入参一律 camelCase（后端 rag_schema.dump() 只转字段名，三份配置 JSON 的键原样透传）；
 * - 枚举/标签集中在此定义（唯一来源，与 common_constants/rag_constant.py 对齐），
 *   业务组件禁止散落魔法字符串。
 */
import {
  MT_AUDIO_TO_TEXT,
  MT_IMAGE_UNDERSTAND,
  MT_MULTIMODAL_EMBEDDING,
  MT_TEXT_EMBEDDING,
  MT_TEXT_RERANK,
  MT_TEXT_TO_TEXT,
  MT_VIDEO_UNDERSTAND,
} from '#/api/ai-workflow/const';
import { defHttp } from '#/api/request';

const BASE = '/api/rag';
const WORKFLOW_BASE = '/api/workflow';

// ==================== 枚举（与 rag_constant.py 一一对应） ====================

/** 知识库类型（创建后永久锁定） */
export type KbType = 'audio_video' | 'doc' | 'image';
export const KB_TYPE_DOC: KbType = 'doc';
export const KB_TYPE_IMAGE: KbType = 'image';
export const KB_TYPE_AUDIO_VIDEO: KbType = 'audio_video';
export const KB_TYPES_ALL: KbType[] = [
  KB_TYPE_DOC,
  KB_TYPE_IMAGE,
  KB_TYPE_AUDIO_VIDEO,
];
export const KB_TYPE_LABELS: Record<string, string> = {
  doc: '文档问答',
  image: '图片搜索',
  audio_video: '音视频搜索',
};

/**
 * 各类型允许后缀兜底（正常由 /runtime-config.allowedExts 下发，此为请求失败时的回退）。
 *
 * doc 型也收图片/音频/视频：这类文件解析引擎抽不出正文，由解析配置里的
 * 音频/视频解析模型先转写成文字，之后的分块与向量化和普通文档同一条链路。
 * 真值在 rag_constant.KB_TYPE_ALLOWED_EXTS（静态对账脚本比对两处）。
 */
export const KB_TYPE_ALLOWED_EXTS_FALLBACK: Record<string, string[]> = {
  doc: [
    'pdf',
    'docx',
    'doc',
    'pptx',
    'ppt',
    'xlsx',
    'xls',
    'csv',
    'md',
    'markdown',
    'html',
    'htm',
    'json',
    'txt',
    'jpg',
    'jpeg',
    'png',
    'webp',
    'bmp',
    'gif',
    'mp3',
    'wav',
    'm4a',
    'aac',
    'flac',
    'mp4',
    'mov',
    'avi',
    'mkv',
    'webm',
  ],
  image: ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'],
  audio_video: [
    'mp3',
    'wav',
    'm4a',
    'aac',
    'flac',
    'mp4',
    'mov',
    'avi',
    'mkv',
    'webm',
  ],
};

/** 解析引擎 */
export const PARSE_ENGINES_ALL = [
  'auto',
  'docling',
  'mineru',
  'native',
] as const;
export const PARSE_ENGINE_LABELS: Record<string, string> = {
  native: '原生解析',
  docling: 'Docling 增强解析',
  mineru: 'MinerU 增强解析',
  auto: '自动选择',
};

/**
 * 分块策略
 *
 * 原「LangChain 递归分块」（recursive）与「自定义分隔符分块」断开点同源、只差一个重叠长度，
 * 已合并到 delimiter（与后端 CHUNK_STRATEGIES_ALL 同清单）。存量库仍可能存着 recursive，
 * 后端会自动按 delimiter 切分，表单回显时也归一成 delimiter。
 */
export const CHUNK_STRATEGIES_ALL = [
  'fixed',
  'delimiter',
  'title',
  'page',
  'semantic',
  'excel',
  'regex',
] as const;
export const CHUNK_STRATEGY_LABELS: Record<string, string> = {
  fixed: '固定长度分块',
  delimiter: '自定义分隔符分块',
  title: '标题层级分块',
  page: '按页分块',
  semantic: '语义分块',
  excel: 'Excel 表头分块',
  regex: '正则分块',
};
/** 已下线的旧策略名 → 合并后的策略名（只为存量库回显，不提供选择入口） */
export const CHUNK_STRATEGY_LEGACY: Record<string, string> = {
  recursive: 'delimiter',
};
/** 只对特定扩展名有意义的策略（页面据此提示） */
export const CHUNK_STRATEGY_EXCLUSIVE_EXT: Record<string, string[]> = {
  page: ['pdf', 'ppt', 'pptx'],
  excel: ['xls', 'xlsx', 'csv'],
};

/** 解析状态机（向量化维度；图谱进度另有 graphState） */
export const DOC_STATUS_ALL = [
  'PENDING',
  'PARSING',
  'ANALYZING',
  'PROCESSING',
  'PROCESSED',
  'FAILED',
] as const;
export const DOC_STATUS_LABELS: Record<string, string> = {
  PENDING: '排队中',
  PARSING: '解析中',
  ANALYZING: '分块中',
  PROCESSING: '向量化中',
  PROCESSED: '已完成',
  FAILED: '失败',
};
export const DOC_STATUS_COLORS: Record<string, string> = {
  PENDING: 'default',
  PARSING: 'processing',
  ANALYZING: 'processing',
  PROCESSING: 'processing',
  PROCESSED: 'success',
  FAILED: 'error',
};

/** 图谱构建态 */
export const KG_STATE_LABELS: Record<number, string> = {
  0: '未构建',
  1: '构建中',
  2: '已构建',
  3: '失败',
};
export const KG_STATE_COLORS: Record<number, string> = {
  0: 'default',
  1: 'processing',
  2: 'success',
  3: 'error',
};

/** 模态 */
export const CHUNK_TYPES_ALL = ['audio_video', 'image', 'text'] as const;
export const CHUNK_TYPE_LABELS: Record<string, string> = {
  text: '文本',
  image: '图片',
  audio_video: '音视频',
};

/**
 * 音频后缀（与后端 rag_constant.RAG_AUDIO_EXTS 同清单，静态对账脚本比对两处）。
 *
 * 对外模态只有 audio_video 一位（文档内嵌音频与独立视频文件合流在同一位上）：检索页
 * 一律用 <video> 渲染会把音频显示成一块黑屏，只能按媒体地址的后缀挑播放器。
 */
export const RAG_AUDIO_EXTS = ['mp3', 'wav', 'm4a', 'aac', 'flac'];

/** 检索模式 */
export const RETRIEVE_MODES_ALL = ['HYBRID', 'KEYWORD', 'VECTOR'] as const;
export const RETRIEVE_MODE_LABELS: Record<string, string> = {
  VECTOR: '向量',
  KEYWORD: '关键词',
  HYBRID: '混合',
};

/**
 * 向量维度：建库即永久锁定，页面只读展示，不提供任何修改入口。
 *
 * 锁定不是前端规矩而是物理限制：ES 索引的 dense_vector dims 建成之后不可改，
 * 换维度等于换向量空间，历史切片全部作废。真值由后端 /runtime-config.vectorDim 下发，
 * 本常量只是那个请求失败时的展示兜底，与 common_constants/rag_constant.py 的
 * RAG_VECTOR_DIM 同值（静态对账脚本会比对两处，写歪当场红）。
 */
export const RAG_VECTOR_DIM = 1024;

// ==================== 通用类型 ====================

export interface PageReq {
  current: number;
  size: number;
}
export interface PageResp<T> {
  records: T[];
  total: number;
  current: number;
  size: number;
}

// ==================== 知识库 ====================

export interface KbPageReq extends PageReq {
  name?: string;
  description?: string;
  /** 留空或 'all' = 不做类型筛选（后端同样容忍 all，不再拿 status 筛库） */
  kbType?: KbType | 'all' | '';
}

export interface KbSaveReq {
  kbType: KbType;
  name: string;
  description?: string;
  embedModelId: number;
  /** 重排模型：配置面板已撤下（需求 15），字段只为接口透传存量值，新建不传 */
  rerankModelId?: number;
  /** 问答/媒体理解模型：doc 型不再用它（已拆成 extract/image 两位），audio_video 型仍是这一位 */
  chatModelId?: number;
  /** 图谱实体抽取模型：doc 型且开了图谱必填 */
  extractModelId?: number;
  /** 图片理解模型：doc 型且解析开关 image_understand 打开必填 */
  imageModelId?: number;
  parserEngine?: string;
  parseConfig?: Record<string, any>;
  chunkConfig?: Record<string, any>;
  retrieveConfig?: Record<string, any>;
  chunkSize?: number;
  chunkOverlap?: number;
  topK?: number;
  scoreThreshold?: number;
  enableGraph?: boolean;
  metadata?: Record<string, any>;
}

export interface KnowledgeBaseResp {
  id: number;
  name: string;
  description?: string;
  kbType: KbType;
  kbTypeLabel?: string;
  tenantId?: number;
  collectionName?: string;
  embedModelId: number;
  embedModelName?: string;
  embeddingDim: number;
  rerankModelId: number;
  chatModelId: number;
  /** 图谱抽取与图片理解已拆成两列（需求 13/15），0=沿用存量 chatModelId */
  extractModelId: number;
  imageModelId: number;
  topK: number;
  scoreThreshold: number;
  chunkSize: number;
  chunkOverlap: number;
  chunkStrategy?: string;
  enableGraph: boolean;
  parserEngine?: string;
  parseConfig: Record<string, any>;
  chunkConfig: Record<string, any>;
  retrieveConfig: Record<string, any>;
  version: number;
  docCount: number;
  chunkCount: number;
  metadata?: Record<string, any>;
  createdBy: number;
  createTime?: string;
  updateTime?: string;
  // 列表侧栏要显示的「创建人 / 归属部门」：后端 attach_creator + attach_dept 补名字
  creatorName?: string;
  ownerDeptId?: number;
  deptName?: string;
  // 当前用户对这一条知识库可执行的动作（后端 decorate_actions 按 ACL 求值下发）
  actions?: string[];
  [k: string]: any;
}

export interface KbOption {
  id: number;
  name: string;
  kbType: KbType;
  kbTypeLabel?: string;
  embeddingModelId: number;
  graphEnabled: boolean;
  docCount: number;
}

export const KbPageList = (data: KbPageReq) =>
  defHttp.post<PageResp<KnowledgeBaseResp>>(
    `${BASE}/knowledge-bases/page`,
    data,
  );

export const KbOptions = () =>
  defHttp.get<KbOption[]>(`${BASE}/knowledge-bases/options`);

export const CreateKb = (data: KbSaveReq) =>
  defHttp.post<number>(`${BASE}/knowledge-bases`, data);

export const GetKbDetail = (kbId: number) =>
  defHttp.get<KnowledgeBaseResp>(`${BASE}/knowledge-bases/${kbId}`);

export const UpdateKb = (kbId: number, data: KbSaveReq) =>
  defHttp.put<boolean>(`${BASE}/knowledge-bases/${kbId}`, data);

// 启用/停用功能已取消（需求 5）：后端不再提供 PUT /{kb_id}/status，
// 库能不能用只看归属、数据范围与 ACL 三层并集

export const DeleteKb = (kbId: number) =>
  defHttp.delete<boolean>(`${BASE}/knowledge-bases/${kbId}`);

// 库内文档（写入口在知识库下，主语是库）
export const DocPageList = (kbId: number, data: DocPageReq) =>
  defHttp.post<PageResp<DocumentResp>>(
    `${BASE}/knowledge-bases/${kbId}/documents/page`,
    data,
  );

export const UploadDocuments = (kbId: number, files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  // 整批原件传完 + 后端落存储并投递解析任务后才回包，默认的 10s 不够（大文件必挂）
  return defHttp.post<UploadResp>(
    `${BASE}/knowledge-bases/${kbId}/documents/upload`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60_000 },
  );
};

export const DeleteDocuments = (kbId: number, ids: number[]) =>
  defHttp.post<{ accepted: number }>(
    `${BASE}/knowledge-bases/${kbId}/documents/delete`,
    { ids },
  );

/** 构建向量：先删该文档的旧向量数据，再按配置重新分块 + 向量化（旧名「重新分块」） */
export const BuildVectors = (kbId: number, data: BuildVectorReq) =>
  defHttp.post<{ accepted: number; purged?: number }>(
    `${BASE}/knowledge-bases/${kbId}/documents/build-vectors`,
    data,
  );

/** 构建图谱：docIds 必填（页面未勾选就不发请求），后端先清旧图数据再重抽（默认只补失败的批） */
export const BuildGraph = (kbId: number, data: GraphBuildReq) =>
  defHttp.post<{ accepted: number; skipped?: number }>(
    `${BASE}/knowledge-bases/${kbId}/graph/build`,
    data,
  );

// ==================== 运行时配置（面板据此置灰/默认值） ====================

export interface RuntimeConfig {
  engines: Record<string, { available: boolean; reason?: string }>;
  strategies: string[];
  parseDefaults: Record<string, any>;
  chunkDefaults: Record<string, any>;
  retrieveDefaults: Record<string, any>;
  vectorDim: number;
  maxChunkChars: number;
  mineruConfigured: boolean;
  esIndex?: string;
  embeddingBatchSize: number;
  /** 开放 API 单次上传文件数上限（当前 100，后端唯一保留的人为上限） */
  openApiMaxFiles: number;
  engineEffective?: Record<string, any>;
  engineOptions?: string[];
  suggestStrategy?: string;
  chunkWarnings?: Record<string, any>;
  titlePathEnabled?: boolean;
  kbTypes?: Array<{ label: string; value: string }>;
  allowedExts?: Record<string, string[]>;
  /**
   * 各解析引擎真读得动的文档格式（数据源是解析层引擎类的 formats 声明）。
   * 面板要按它写清 native/docling/mineru 的覆盖范围，前端自己抄一份必然与引擎代码漂移。
   */
  engineFormats?: Record<string, string[]>;
  /** 媒体后缀按种类分组（image/audio/video）：三个解析增强开关的提示按它说各自管哪一类文件 */
  mediaExts?: Record<string, string[]>;
  mediaKindLabels?: Record<string, string>;
  chunkTypes?: string[];
  docStatuses?: string[];
  graphEntityTypes?: string[];
  maxFileSizeMb?: number;
  graphVizDefaultLimit?: number;
  /** 一次大模型调用喂多少条切片（分批粒度，不限制总切片数） */
  graphExtractBatchChunks?: number;
  embeddingConcurrency?: number;
  configLoaded?: boolean;
}

export const GetRuntimeConfig = (params?: {
  engine?: string;
  ext?: string;
  kbType?: KbType;
}) =>
  defHttp.get<RuntimeConfig>(`${BASE}/knowledge-bases/runtime-config`, {
    params,
  });

// ==================== 文档 ====================

export interface DocPageReq extends PageReq {
  name?: string;
  status?: string;
  graphState?: number;
}

export interface DocumentResp {
  id: number;
  kbId: number;
  title?: string;
  fileName?: string;
  fileExt?: string;
  fileSize: number;
  contentType?: string;
  mediaType?: 'audio_video' | 'image' | 'text';
  parserEngine?: string;
  parseVersion: number;
  status?: string;
  statusLabel?: string;
  vectorized: boolean;
  graphState: number;
  graphStateLabel?: string;
  chunkCount: number;
  pageCount: number;
  mediaDuration: number;
  mediaSummary?: string;
  /** 原件签名地址：只在详情接口下发，列表已不再补（列表不自动加载多媒体，预览走 GetDocOriginal） */
  mediaUrl?: string;
  taskId?: string;
  errorMsg?: string;
  createdBy: number;
  /** 创建人名字：后端 attach_creator 在 DTO 之外挂进来的，列表「创建人」列用它，别拿数字 id 给人看 */
  creatorName?: string;
  createTime?: string;
  updateTime?: string;
  /** 当前用户对这一条文档可执行的动作（后端列表接口按 ACL 求值下发） */
  actions?: string[];
}

export interface DocActionReq {
  docIds: number[];
  reparse?: boolean;
}

export interface BuildVectorReq extends DocActionReq {
  chunkConfig?: Record<string, any>;
  chunkSize?: number;
  chunkOverlap?: number;
}

export interface DocProgressResp {
  docId: number;
  // ---------- 向量化维度（进度条分段就按 stages 画） ----------
  stage: string;
  stageLabel?: string;
  percent: number;
  total: number;
  done: number;
  message?: string;
  updateTime?: string;
  status?: string;
  statusLabel?: string;
  stages?: Array<Record<string, any>>;
  // ---------- 图谱维度（与向量化完整分开两个 Redis 键） ----------
  graphStage?: string;
  graphStageLabel?: string;
  graphPercent?: number;
  graphTotal?: number;
  graphDone?: number;
  graphMessage?: string;
  graphStages?: Array<Record<string, any>>;
  graphState?: number;
  graphStateLabel?: string;
}

export interface UploadItemResp {
  fileName: string;
  docId: number;
  taskId?: string;
  status?: string;
  message?: string;
}

export interface UploadResp {
  kbId: number;
  accepted: number;
  rejected: number;
  items: UploadItemResp[];
}

export const GetDocsProgressBatch = (ids: number[]) =>
  defHttp.post<DocProgressResp[]>(`${BASE}/documents/progress/batch`, { ids });

export const GetDocContent = (docId: number) =>
  defHttp.get<{ content: string; truncated?: boolean }>(
    `${BASE}/documents/${docId}/content`,
  );

/** 预览原文件（列表行的 preview 动作）：回签名地址 + mediaType，前端据此选打开方式 */
export interface DocOriginalResp {
  docId: number;
  docName?: string;
  fileName?: string;
  fileExt?: string;
  fileSize?: number;
  contentType?: string;
  mediaType?: string;
  url: string;
}

export const GetDocOriginal = (docId: number) =>
  defHttp.get<DocOriginalResp>(`${BASE}/documents/${docId}/original`);

export const RenameDoc = (docId: number, title: string) =>
  defHttp.put<boolean>(`${BASE}/documents/${docId}/title`, { title });

export const RetryDocs = (data: DocActionReq) =>
  defHttp.post<{ accepted: number; rejected: number }>(
    `${BASE}/documents/retry`,
    data,
  );

export const PageChunks = (docId: number, data: ChunkPageReq) =>
  defHttp.post<PageResp<ChunkResp>>(
    `${BASE}/documents/${docId}/chunks/page`,
    data,
  );

export const UpdateChunk = (chunkId: number, data: ChunkUpdateReq) =>
  defHttp.put<ChunkResp>(`${BASE}/documents/chunks/${chunkId}`, data);

/** 删除切片：后端一次清完元数据（MySQL）+ 向量（ES）+ 图谱（Neo4j）三份账 */
export const DeleteChunk = (chunkId: number) =>
  defHttp.delete<Record<string, any>>(`${BASE}/documents/chunks/${chunkId}`);

// ==================== 切片 ====================

export interface ChunkPageReq extends PageReq {
  kbId?: number;
  chunkType?: string;
  keyword?: string;
  available?: number;
}

export interface ChunkUpdateReq {
  content?: string;
  available?: number;
}

export interface ChunkResp {
  id: number;
  kbId: number;
  docId: number;
  chunkIndex: number;
  chunkType: string;
  chunkTypeLabel?: string;
  content: string;
  embedText?: string;
  titlePath?: string;
  sheetName?: string;
  pageNum: number;
  blockId?: string;
  mediaUrl?: string;
  mediaType?: string;
  tokenCount: number;
  available: boolean;
  vectorized: boolean;
  extra?: Record<string, any>;
  createTime?: string;
}

// ==================== 检索 ====================

export interface RetrieveReq {
  kbIds?: number[];
  query?: string;
  imageUrl?: string;
  chunkType?: string;
  mode?: string;
  topK?: number;
  scoreThreshold?: number;
  rerank?: boolean;
  vectorWeight?: number;
  withGraph?: boolean;
}

export interface RetrieveHit {
  kbId: number;
  kbName?: string;
  docId: number;
  docName?: string;
  chunkId: number;
  chunkIndex: number;
  chunkType: string;
  content: string;
  score: number;
  vectorScore: number;
  keywordScore: number;
  titlePath?: string;
  pageNum: number;
  sheetName?: string;
  mediaUrl?: string;
  mediaDuration: number;
  blockId?: string;
}

export interface RetrieveResp {
  query?: string;
  mode: string;
  total: number;
  tookMs: number;
  kbIds: number[];
  hits: RetrieveHit[];
  warnings: string[];
  graph: Record<string, any>;
}

export const Retrieve = (data: RetrieveReq) =>
  defHttp.post<RetrieveResp>(`${BASE}/retrieve`, data);

export const RetrieveRows = (data: RetrieveReq) =>
  defHttp.post<RetrieveHit[]>(`${BASE}/retrieve/rows`, data);

/**
 * 图搜图：把本地查询图片上传到公共存储，换取匿名可访问 URL 再喂给 /retrieve。
 * 复用工作流的通用上传口（后端只吃句柄，不吃 base64，见 rag_schema.RetrieveReq 注释）。
 */
export async function uploadQueryImage(file: File): Promise<string> {
  const form = new FormData();
  form.append('file', file);
  const info: any = await defHttp.post(
    `${WORKFLOW_BASE}/workflow-files/upload`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60_000 },
  );
  const url = info?.url || info?.data?.url;
  if (!url) throw new Error('上传成功但未返回可访问地址');
  return url;
}

// ==================== 图谱 ====================

export interface GraphQueryReq {
  kbIds?: number[];
  docIds?: number[];
  keyword?: string;
  entityType?: string;
  depth?: number;
  limit?: number;
}

export interface GraphBuildReq {
  /** 页面必须勾选目标文档（未勾选不得发请求，后端也会报「请选择目标文档」） */
  docIds: number[];
  /** 恒为真：构建图谱就是先清旧图数据再重抽 */
  force?: boolean;
  /** 断点续抽（缺省 true）：跳过上一轮已抽成功的批，不重烧那几批的模型调用 */
  resume?: boolean;
}

export interface GraphNodeResp {
  id: string;
  name: string;
  label: string;
  entityType?: string;
  kbId: number;
  summary: string;
  aliases: string[];
  weight: number;
}

export interface GraphEdgeResp {
  source: string;
  target: string;
  relation: string;
  name?: string;
  kbId: number;
  docIds: number[];
  weight: number;
}

export interface GraphResp {
  nodes: GraphNodeResp[];
  edges: GraphEdgeResp[];
  totalNodes: number;
  totalEdges: number;
  truncated: boolean;
}

export const GraphQuery = (data: GraphQueryReq) =>
  defHttp.post<GraphResp>(`${BASE}/graph/query`, data);

/** 图谱规模概览（图谱检索页顶部那几个计数） */
export const GraphStatistics = (params: { kbIds?: string }) =>
  defHttp.get<Record<string, any>>(`${BASE}/graph/statistics`, { params });

/** 实体检索（图谱检索页的搜索框，不依赖子图查询） */
export const GraphEntitySearch = (params: {
  entityType?: string;
  kbIds?: string;
  keyword: string;
  limit?: number;
}) =>
  defHttp.get<Record<string, any>[]>(`${BASE}/graph/entities/search`, {
    params,
  });

/** 实体溯源（图谱弹窗点节点时拉原文切片位置）：搜索框走 /graph/query 的 keyword，不另开口 */
export const GraphEntitySources = (params: {
  entityType?: string;
  kbIds?: string;
  limit?: number;
  name: string;
}) =>
  defHttp.get<Record<string, any>[]>(`${BASE}/graph/entities/sources`, {
    params,
  });

// ==================== 模型下拉（复用 workflow /models/list） ====================

export interface ModelOption {
  id: number;
  provider: string;
  type: string;
  name: string;
  modelName?: string;
}

const listModels = (type: string) =>
  defHttp.get<ModelOption[]>(`${WORKFLOW_BASE}/models/list`, {
    params: { type },
  });

const toOption = (m: ModelOption) => ({
  label: `${m.provider} / ${m.name}`,
  value: m.id,
});

/** 拉一个细分码的模型列表并转下拉项（三个拉模型的入口同一个形状，不各写一遍） */
async function optionsOf(type: string) {
  const rows = await listModels(type);
  return rows.map((m) => toOption(m));
}

/**
 * 按知识库类型取可选向量模型。
 * doc：文本向量 + 多模态向量皆可；image/audio_video：只能多模态向量（跨模态同空间）。
 * 与 rag_constant.KB_TYPE_MODEL_REQ 的 embed_any 对齐。
 * 模型列表不下发维度，维度能不能建库由后端 /knowledge-bases 建库时把关。
 */
export async function fetchEmbedModels(kbType: KbType) {
  const codes =
    kbType === KB_TYPE_DOC
      ? [MT_TEXT_EMBEDDING, MT_MULTIMODAL_EMBEDDING]
      : [MT_MULTIMODAL_EMBEDDING];
  const groups = await Promise.all(codes.map((c) => optionsOf(c)));
  const merged = groups.flat();
  // 去重（同模型可能同时在两个分类里登记）
  const seen = new Set<number>();
  return merged.filter((o) => !seen.has(o.value) && !!seen.add(o.value));
}

export const fetchRerankModels = () => optionsOf(MT_TEXT_RERANK);

export const fetchChatModels = () => optionsOf(MT_TEXT_TO_TEXT);

/** 图谱实体抽取模型：能力形态与问答模型同族（文生文），但配置页上独立一位 */
export const fetchExtractModels = () => optionsOf(MT_TEXT_TO_TEXT);

/** 图片理解模型（解析开关 image_understand 打开后必填） */
export const fetchImageModels = () => optionsOf(MT_IMAGE_UNDERSTAND);

/** 音视频理解模型（audio_video 型生成摘要用） */
export const fetchUnderstandModels = () => optionsOf(MT_VIDEO_UNDERSTAND);

/**
 * 音频解析模型（doc 型库收到音频文件时转写成正文）。
 * 与上面的 fetchUnderstandModels 是两个能力：一个是音频转文字（ASR），一个是看视频说话。
 */
export const fetchAudioModels = () => optionsOf(MT_AUDIO_TO_TEXT);

/** 视频解析模型（doc 型库收到视频文件时描述成正文），与媒体摘要复用同一个能力 */
export const fetchVideoModels = () => optionsOf(MT_VIDEO_UNDERSTAND);
