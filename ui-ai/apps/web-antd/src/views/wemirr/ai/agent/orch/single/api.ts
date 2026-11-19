import { defHttp } from '#/api/request';

/**
 * 单智能体编排前端接口层。
 *
 * 复用深度探索（harness）的会话/对话/SSE 管线做「验证台」：
 * 发消息走 /api/harness/chat，多带 orchMode=single + orchConfigId，worker 据此
 * 用该智能体配置跑单 Agent；订阅/中断端点与深度探索完全一致（见 ./ValidateConsole.vue）。
 */
const BASE_URL = '/api/harness/orch/agent';

/** 挂载快照（四类能力 id + 模型 id） */
export interface OrchMounts {
  kbIds: number[];
  mcpIds: number[];
  toolIds: number[];
  skillIds: number[];
  modelId?: null | number;
}

export interface OrchAgentItem {
  id: number;
  name: string;
  purpose?: null | string;
  systemPrompt?: null | string;
  mounts?: Partial<OrchMounts>;
  status: number;
  created_by?: number;
  updated_by?: number;
  create_time?: string;
  update_time?: string;
  /** 当前用户对本行可执行的动作（后端按 ACL 求值下发） */
  actions?: string[];
  creatorName?: string;
}

export interface OrchAgentSaveReq {
  name: string;
  purpose?: null | string;
  systemPrompt?: null | string;
  mounts?: Partial<OrchMounts>;
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
}) => defHttp.get<{ items: OrchAgentItem[]; total: number }>(`${BASE_URL}/page`, { params });

export const GetDetail = (id: number) =>
  defHttp.get<OrchAgentItem>(`${BASE_URL}/${id}/detail`);

export const AddObj = (data: OrchAgentSaveReq) =>
  defHttp.post<{ id: number }>(`${BASE_URL}/create`, data);

export const UpdateObj = (id: number, data: OrchAgentSaveReq) =>
  defHttp.put(`${BASE_URL}/${id}/modify`, data);

export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

/** 四类能力候选（use 口径：自建 ∪ 授权；skill/mcp 另带 code/type） */
export const GetUsableResources = (kind: 'kb' | 'mcp' | 'skill' | 'tool') =>
  defHttp.get<{ items: ResourceOption[]; total: number }>(
    `${BASE_URL}/usable-resources/${kind}`,
  );

// ==================== 验证台：复用 harness 会话/对话/SSE ====================

/** 新建验证会话（harness session 只存标题，模型/挂载逐轮携带） */
export const CreateSession = (title: string) =>
  defHttp.post<{ id: number }>('/api/harness/session', { title });

/** 发送一轮：带 orchMode=single + orchConfigId，worker 用该智能体配置跑单 Agent */
export const OrchChat = (body: {
  message: string;
  modelId: number;
  orchConfigId: number;
  orchMode: string;
  sessionId: number;
}) => defHttp.post<{ turnId: string }>('/api/harness/chat', body);

/** 中断本轮 */
export const OrchInterrupt = (turnId: string) =>
  defHttp.post(`/api/harness/chat/${turnId}/interrupt`);

/** SSE 订阅地址（EventSource 带不了登录头，网关按 /subscribe 后缀放行） */
export const SubscribeUrl = (turnId: string) =>
  `/api/harness/chat/${turnId}/subscribe`;

/** 会话消息历史（回放用） */
export const SessionMessages = (sessionId: number) =>
  defHttp.get<any[]>(`/api/harness/session/${sessionId}/messages`);

/** 可选模型清单（模型 picker 数据源，本人已授权且支持工具调用） */
export const ListModels = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>('/api/harness/models');
