import { defHttp } from '#/api/request';

/**
 * 单智能体编排前端接口层。
 *
 * 列表页的「验证」按钮与验证台已删除（验证能力包含在「编辑与验证」这个授权类型里），
 * 使用者统一走深度探索页：发消息走 /api/harness/chat，多带 orchMode=single +
 * orchConfigId，worker 据此用该智能体配置跑单 Agent。
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
  name?: string;
  purpose?: string;
  page: number;
  page_size: number;
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

// ==================== 模型候选（表单抽屉的模型 picker） ====================

/** 本人已授权且支持工具调用的模型清单 */
export const ListModels = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>('/api/harness/models');
