import { defHttp } from '#/api/request';

/**
 * 团队模式编排前端接口层。
 *
 * 成员来源两条路：从单智能体克隆（sourceAgentId，后端逐成员 use 口径过滤无权能力）
 * 或页面自定义。验证台复用深度探索 SSE 管线（/api/harness/chat 带 orchMode=team +
 * orchConfigId），worker 据此跑 TeamPipeline。
 */
const BASE_URL = '/api/harness/orch/team';

/** 挂载快照（四类能力 id + 模型 id） */
export interface OrchMounts {
  kbIds: number[];
  mcpIds: number[];
  toolIds: number[];
  skillIds: number[];
  modelId?: null | number;
}

/** 成员/队长规格：克隆(sourceAgentId) 或自定义（name/systemPrompt/mounts 覆盖） */
export interface OrchMemberSpec {
  sourceAgentId?: null | number;
  name?: null | string;
  description?: null | string;
  systemPrompt?: null | string;
  mounts?: Partial<OrchMounts>;
}

export interface OrchTeamItem {
  id: number;
  name: string;
  purpose?: null | string;
  leaderName?: null | string;
  memberCount?: number;
  status: number;
  created_by?: number;
  create_time?: string;
  update_time?: string;
  actions?: string[];
  creatorName?: string;
}

export interface OrchTeamDetail {
  id: number;
  name: string;
  purpose?: null | string;
  status: number;
  config: {
    leader?: OrchMemberSpec & { mounts?: OrchMounts };
    members?: Array<OrchMemberSpec & { index?: number; mounts?: OrchMounts }>;
    reset_members?: boolean;
  };
}

export interface OrchTeamSaveReq {
  name: string;
  purpose?: null | string;
  leader: OrchMemberSpec;
  members: OrchMemberSpec[];
  status?: boolean;
}

/** 挂载/模型候选（后端 usable-resources 平铺返回） */
export interface ResourceOption {
  id: number;
  name: string;
  description?: null | string;
  code?: string;
  type?: string;
}

// ==================== CRUD ====================
export const PageList = (params: {
  keyword?: string;
  page: number;
  page_size: number;
  status?: number;
}) => defHttp.get<{ items: OrchTeamItem[]; total: number }>(`${BASE_URL}/page`, { params });

export const GetDetail = (id: number) =>
  defHttp.get<OrchTeamDetail>(`${BASE_URL}/${id}/detail`);

export const AddObj = (data: OrchTeamSaveReq) =>
  defHttp.post<{ id: number }>(`${BASE_URL}/create`, data);

export const UpdateObj = (id: number, data: OrchTeamSaveReq) =>
  defHttp.put(`${BASE_URL}/${id}/modify`, data);

export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

/** 成员挂载候选（use 口径：自建 ∪ 授权） */
export const GetUsableResources = (kind: 'kb' | 'mcp' | 'skill' | 'tool') =>
  defHttp.get<{ items: ResourceOption[]; total: number }>(
    `${BASE_URL}/usable-resources/${kind}`,
  );

/** 克隆来源候选：单智能体列表（当前用户可见，走团队侧 clone-sources，仅需 team:list） */
export const ListAgentsForClone = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>(`${BASE_URL}/clone-sources`);

/** 单智能体详情（克隆时预填成员 name/systemPrompt/mounts 用） */
export const GetAgentDetail = (id: number) =>
  defHttp.get<any>(`${BASE_URL}/clone-source/${id}`);

// ==================== 验证台：复用 harness 会话/对话/SSE ====================
export const CreateSession = (title: string) =>
  defHttp.post<{ id: number }>('/api/harness/session', { title });

/** 发送一轮：带 orchMode=team + orchConfigId，worker 跑 TeamPipeline */
export const OrchChat = (body: {
  message: string;
  modelId: number;
  orchConfigId: number;
  orchMode: string;
  sessionId: number;
}) => defHttp.post<{ turnId: string }>('/api/harness/chat', body);

export const OrchInterrupt = (turnId: string) =>
  defHttp.post(`/api/harness/chat/${turnId}/interrupt`);

export const SubscribeUrl = (turnId: string) =>
  `/api/harness/chat/${turnId}/subscribe`;

export const ListModels = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>('/api/harness/models');
