import { defHttp } from '#/api/request';

// ==================== 类型定义 ====================

/** 会话保存响应 */
export interface ConversationSaveRep {
  id: string;
}

/** 会话类型枚举 */
export enum ConversationType {
  /** 通用智能体对话 */
  AGENT = 2,
  /** 图片生成 */
  IMAGE_GEN = 5,
  /** 知识库对话 */
  KNOWLEDGE_BASE = 4,
  /** 普通对话 */
  NORMAL = 1,
  /** 平台智能体（客服等定制的） */
  PLATFORM_AGENT = 3,
}

/** 会话分页请求 */
export interface ConversationPageReq {
  current: number;
  size: number;
  title?: string;
  modelName?: string;
  knowledgeBaseId?: number;
  type?: number;
  types?: number[];
}

/** 会话分页响应 */
export interface ConversationPageRep {
  id: string;
  title: string;
  modelName: null | string;
  knowledgeBaseId: null | string;
  lastMessage: null | string;
  messageCount: number;
  pinned: boolean;
  type: ConversationType;
  createTime: string;
  updatedTime: null | string;
}

/** 会话保存请求 */
export interface ConversationSaveReq {
  title: string;
  agentId?: number;
  knowledgeBaseId?: number;
  type: number;
}

/** 会话消息响应 */
export interface ConversationMessageRep {
  id: string;
  role: 'ASSISTANT' | 'USER';
  userInput: null | string;
  finalPrompt: null | string;
  modelOutput: null | string;
  displayContent: null | string;
  thinkingContent: null | string;
  references?: ChatReference[];
  variables?: {
    references?: ChatReference[];
    [key: string]: any;
  };
  createTime: string;
}

export interface ChatReference {
  chunkId?: string;
  documentId?: string;
  documentName?: string;
  icon?: string;
  itemId?: string;
  metadata?: Record<string, any>;
  score?: number;
  siteName?: string;
  snippet?: string;
  title?: string;
  type?: 'document' | 'graph' | 'web' | string;
  url?: string;
}

/** AI对话请求参数 */
export interface AskReq {
  chatType: ConversationType;
  modelId?: number;
  agentId?: number;
  kbId?: number;
  conversationId?: string;
  prompt: string;
  enableWebSearch?: boolean;
  returnThinking?: boolean;
  metadataFilter?: Record<string, string>;
  imageUrl?: string;
}

/** 模型配置 */
export interface ModelConfig {
  id: number;
  provider: string;
  type: string;
  name: string;
  baseUrl: string;
}

/** 智能体 */
export interface ChatAgent {
  id: number;
  name: string;
  avatar: string;
  description: string;
}

// ==================== API 接口 ====================

/** 分页查询会话列表 */
export function getConversationPage(params: ConversationPageReq) {
  return defHttp.post<{ records: ConversationPageRep[]; total: number }>(
    '/api/workflow/conversations/page',
    params,
  );
}

/** 获取会话消息列表 */
export function getConversationMessages(id: string) {
  return defHttp.get<ConversationMessageRep[]>(
    `/api/workflow/conversations/${id}/messages`,
  );
}

/** 创建新会话 */
export function createConversation(data: ConversationSaveReq) {
  return defHttp.post<ConversationSaveRep>('/api/workflow/conversations/create', data);
}

/** 修改会话 */
export function updateConversation(id: string, data: ConversationSaveReq) {
  return defHttp.put(`/api/workflow/conversations/${id}/modify`, data);
}

/** 删除会话 */
export function deleteConversation(id: string) {
  return defHttp.delete(`/api/workflow/conversations/${id}`);
}

/** 清空会话消息 */
export function clearConversationMessages(id: string) {
  return defHttp.delete(`/api/workflow/conversations/${id}/messages`);
}

/** 置顶会话 */
export function pinConversation(id: string) {
  return defHttp.put(`/api/workflow/conversations/${id}/pin`);
}

/** 取消置顶会话 */
export function unpinConversation(id: string) {
  return defHttp.put(`/api/workflow/conversations/${id}/unpin`);
}

/** 获取模型列表 */
export function getModelList() {
  return defHttp.get<ModelConfig[]>('/api/workflow/models/list?type=text');
}

/** 获取我的智能体列表 */
export function getMyAgents() {
  return defHttp.get<ChatAgent[]>('/api/workflow/chat-agents/mine');
}

/** 获取SSE流式对话URL */
export function getChatStreamUrl() {
  return '/api/workflow/chat/stream';
}
