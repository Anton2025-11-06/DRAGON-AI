import type { ConversationMessageRep } from '../api';

import { defHttp } from '#/api/request';

import { ConversationType } from '../api';

// ==================== 类型定义 ====================

/** 知识库信息 */
export interface KnowledgeBase {
  id: number;
  name: string;
  description: string;
  topK: number;
  scoreThreshold: number;
  chatModelId: number;
  embedModelId: number;
  createTime: string;
  lastModifyTime?: string;
}

/** 知识库对话请求参数 */
export interface RagAskReq {
  chatType: ConversationType;
  kbId: number;
  prompt: string;
  enableWebSearch?: boolean;
  returnThinking?: boolean;
}

// ==================== API 接口 ====================

/** 分页查询知识库列表 */
export function getKnowledgeBasePage(params: {
  current: number;
  size: number;
}) {
  return defHttp.post<{ records: KnowledgeBase[]; total: number }>(
    '/api/rag/knowledge-bases/page',
    params,
  );
}

/** 根据知识库ID获取消息列表 */
export function getMessagesByKbId(kbId: number) {
  return defHttp.get<ConversationMessageRep[]>(
    `/api/rag/conversations/messages?kbId=${kbId}`,
  );
}

/** 获取RAG流式对话URL */
export function getRagChatStreamUrl() {
  return '/api/rag/chat/stream';
}
