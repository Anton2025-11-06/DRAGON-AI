/**
 * RAG 文档管理 API
 * 包含知识库、文档、图谱相关接口
 */
import { defHttp } from '#/api/request';

// ==================== 知识库相关 ====================

/** 知识库分页请求 */
export interface KnowledgeBasePageReq {
  current: number;
  size: number;
  name?: string;
  description?: string;
}

/** 知识库响应 */
export interface KnowledgeBaseResp {
  id: number;
  name: string;
  description: string;
  tenantId?: number;
  collectionName?: string;
  embedModelId: number;
  topK: number;
  scoreThreshold: number;
  rerankModelId?: number;
  chunkSize: number;
  chunkOverlap: number;
  enableGraph: boolean;
  chatModelId?: number;
  version?: number;
  metadata?: Record<string, any>;
  createTime?: string;
}

/** 知识库保存请求 */
export interface KnowledgeBaseSaveReq {
  name: string;
  description?: string;
  embedModelId: number;
  topK?: number;
  scoreThreshold?: number;
  rerankModelId?: number;
  chunkSize?: number;
  chunkOverlap?: number;
  enableGraph?: boolean;
  chatModelId?: number;
  metadata?: Record<string, any>;
}

/** 语义搜索响应 */
export interface EmbeddingMatchRep {
  content: string;
  score: number;
  metadata: Record<string, any>;
  searchType: string;
}

/** 分页查询知识库 */
export const KnowledgeBasePageList = (params: KnowledgeBasePageReq) =>
  defHttp.post<{ records: KnowledgeBaseResp[]; total: number }>(
    '/api/rag/knowledge-bases/page',
    params,
  );

/** 获取知识库详情 */
export const GetKnowledgeBaseInfo = (id: number) =>
  defHttp.get<KnowledgeBaseResp>(`/api/rag/knowledge-bases/${id}/detail`);

/** 新增知识库 */
export const AddKnowledgeBase = (data: KnowledgeBaseSaveReq) =>
  defHttp.post('/api/rag/knowledge-bases/create', data);

/** 更新知识库 */
export const UpdateKnowledgeBase = (id: number, data: KnowledgeBaseSaveReq) =>
  defHttp.put(`/api/rag/knowledge-bases/${id}/modify`, data);

/** 删除知识库 */
export const DelKnowledgeBase = (id: number) =>
  defHttp.delete(`/api/rag/knowledge-bases/${id}`);

/** 获取聊天模型列表（归属 service_workflow） */
export const GetChatModels = () =>
  defHttp.get('/api/workflow/models/list', { params: { type: 'TEXT' } });

/** 获取嵌入模型列表 */
export const GetEmbeddingModels = () =>
  defHttp.get('/api/workflow/models/list', { params: { type: 'EMBEDDING' } });

/** 获取重排序模型列表 */
export const GetRerankModels = () =>
  defHttp.get('/api/workflow/models/list', { params: { type: 'RERANK' } });

/** 语义搜索 */
export const SemanticSearch = (kbId: number, query: string, topK: number = 5) =>
  defHttp.get<EmbeddingMatchRep[]>('/api/rag/knowledge-search/semantic-search', {
    params: { kbId, query, topK },
  });

// ==================== 文档相关 ====================

export interface DocumentPageReq {
  current: number;
  size: number;
  column?: string;
  asc?: boolean;
  knowledgeBaseId?: number;
  title?: string;
  status?: 'FAILED' | 'PENDING' | 'PROCESSED' | 'PROCESSING';
}

export interface DocumentPageRep {
  id: number;
  kbId: number;
  title: string;
  fileSize: number;
  contentType: string;
  filePath?: string;
  status: 'FAILED' | 'PENDING' | 'PROCESSED' | 'PROCESSING';
  vectorized: boolean;
  graphed: boolean;
  chunkCount: number;
  createTime: string;
  lastModifyTime?: string;
}

export interface DocumentDetailRep {
  id: number;
  kbId: number;
  title: string;
  fileSize: number;
  contentType: string;
  filePath?: string;
  status: 'FAILED' | 'PENDING' | 'PROCESSED' | 'PROCESSING';
  vectorized: boolean;
  graphed: boolean;
  content: string;
  chunkCount: number;
  createTime: string;
  lastModifyTime?: string;
}

export interface DocumentModifyReq {
  title: string;
}

// 向量化状态响应
export interface VectorizeStatusRep {
  itemId: number;
  vectorized: boolean;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  errorMessage?: string;
  taskId?: string;
}

// 分页获取文档列表
export const PageList = (params: DocumentPageReq) => {
  const { knowledgeBaseId, ...queryParams } = params;

  return defHttp.get<{ records: DocumentPageRep[]; total: number }>(
    `/api/rag/rag-documents/${knowledgeBaseId}/page`,
    { params: queryParams },
  );
};

// 获取文档详情
export const GetInfo = (documentId: number) =>
  defHttp.get<DocumentDetailRep>(`/api/rag/rag-documents/${documentId}/detail`);

// 修改文档
export const UpdateObj = (id: number, data: DocumentModifyReq) =>
  defHttp.put(`/api/rag/rag-documents/${id}`, data);

// 删除文档
export const DelObj = (id: number) => defHttp.delete(`/api/rag/rag-documents/${id}`);

// 上传文档到知识库
export const UploadDocument = (knowledgeBaseId: number, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('kbId', knowledgeBaseId.toString());
  return defHttp.post('/api/rag/rag-documents/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
};

// 提交文档向量化任务(异步)
export const VectorizeDocument = (documentId: number) =>
  defHttp.post<string>(`/api/rag/vectorization/knowledge-item/${documentId}`);

// 查询文档向量化状态
export const GetVectorizeStatus = (documentId: number) =>
  defHttp.get<VectorizeStatusRep>(
    `/api/rag/vectorization/${documentId}/vectorize-status`,
  );

// 批量向量化文档
export const BatchVectorizeDocuments = (documentIds: number[]) =>
  defHttp.post('/api/rag/vectorization/knowledge-items', documentIds);

// 文档分块预览响应
export interface PreviewChunkResp {
  id: number;
  content: string;
}

// 文档完整内容预览
export const PreviewDocument = (itemId: number) =>
  defHttp.post<string>(`/api/rag/rag-documents/preview/${itemId}`);

// 文档分块预览
export const PreviewDocumentChunk = (itemId: number) =>
  defHttp.post<PreviewChunkResp[]>(
    `/api/rag/rag-documents/preview-chunk/${itemId}/`,
  );

// ==================== 图谱化相关接口 ====================

/**
 * 图谱化处理结果
 */
export interface GraphResult {
  itemId: number;
  nodesCreated: number;
  relationshipsCreated: number;
  success: boolean;
  message?: string;
}

/**
 * 图谱节点
 */
export interface GraphNode {
  id: string;
  label: string;
  name: string;
  labels: string[];
  properties: Record<string, any>;
}

/**
 * 图谱边（关系）
 */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  properties: Record<string, any>;
}

/**
 * 图谱可视化数据响应
 */
export interface GraphVisualizationResp {
  knowledgeBaseId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  nodeCount: number;
  edgeCount: number;
}

/**
 * 召回的实体
 */
export interface RecalledEntity {
  id: string;
  name: string;
  type: string;
  score: number;
}

/**
 * 实体召回测试响应
 */
export interface EntityRecallResp {
  knowledgeBaseId: string;
  keywords: string[];
  entities: RecalledEntity[];
  triples: string[];
  entityCount: number;
  tripleCount: number;
}

/**
 * 对知识条目进行图谱化
 */
export const GraphizeKnowledgeItem = (itemId: number) =>
  defHttp.post<GraphResult>(`/api/rag/graph/knowledge-item/${itemId}`);

/**
 * 批量对知识条目进行图谱化
 */
export const BatchGraphizeKnowledgeItems = (itemIds: number[]) =>
  defHttp.post<GraphResult[]>('/api/rag/graph/knowledge-items', itemIds);

/**
 * 删除知识条目的图谱数据
 */
export const DeleteGraphForItem = (itemId: number) =>
  defHttp.delete<boolean>(`/api/rag/graph/knowledge-item/${itemId}`);

/**
 * 删除知识库的所有图谱数据
 */
export const DeleteGraphForKnowledgeBase = (kbId: number) =>
  defHttp.delete<boolean>(`/api/rag/graph/knowledge-base/${kbId}`);

/**
 * 获取知识库的图谱统计信息
 */
export const GetGraphStatistics = (kbId: number) =>
  defHttp.get<Record<string, any>>(
    `/api/rag/graph/knowledge-base/${kbId}/statistics`,
  );

/**
 * 获取知识库的图谱 Schema
 */
export const GetGraphSchema = (kbId: number) =>
  defHttp.get<string>(`/api/rag/graph/knowledge-base/${kbId}/schema`);

/**
 * 检查图谱服务健康状态
 */
export const CheckGraphHealth = () => defHttp.get<boolean>('/api/rag/graph/health');

/**
 * 获取图谱可视化数据
 */
export const GetVisualizationData = (kbId: number, limit: number = 100) =>
  defHttp.get<GraphVisualizationResp>(
    `/api/rag/graph/knowledge-base/${kbId}/visualization`,
    {
      params: { limit },
    },
  );

/**
 * 实体召回测试
 */
export const TestEntityRecall = (kbId: number, keywords: string[]) =>
  defHttp.post<EntityRecallResp>(
    `/api/rag/graph/knowledge-base/${kbId}/recall-test`,
    keywords,
  );
