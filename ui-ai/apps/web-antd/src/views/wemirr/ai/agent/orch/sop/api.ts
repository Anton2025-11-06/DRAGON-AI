import { defHttp } from '#/api/request';

/**
 * SOP 模式编排前端接口层。
 *
 * 流程节点：名称/流程定义/执行者/检查者/最大驳回次数（默认 3）。执行者/检查者来源两条路：
 * 从单智能体克隆（sourceAgentId，后端逐成员 use 口径过滤无权能力）或页面自定义。列表页的
 * 「验证」按钮已删除（验证能力包含在「编辑与验证」这个授权类型里），SOP 统一在深度探索页
 * 运行：发消息带 orchMode=sop + orchConfigId，worker 据此跑
 * SOPEngine（顺序流程，所有节点共用一份 state）。
 */
const BASE_URL = '/api/harness/orch/sop';

/** 挂载快照（四类能力 id + 模型 id） */
export interface OrchMounts {
  kbIds: number[];
  mcpIds: number[];
  toolIds: number[];
  skillIds: number[];
  modelId?: null | number;
}

/** 执行者/检查者规格：克隆(sourceAgentId) 或自定义（name/systemPrompt/mounts 覆盖） */
export interface OrchMemberSpec {
  sourceAgentId?: null | number;
  name?: null | string;
  description?: null | string;
  systemPrompt?: null | string;
  mounts?: Partial<OrchMounts>;
}

/** SOP 流程节点：名称/流程定义/执行者/检查者/最大驳回次数 */
export interface OrchSopStepSpec {
  subject: string;
  description?: null | string;
  executor: OrchMemberSpec;
  verifier?: null | OrchMemberSpec;
  maxAttempts: number;
}

export interface OrchSopItem {
  id: number;
  name: string;
  purpose?: null | string;
  stepCount?: number;
  firstStep?: null | string;
  status: number;
  created_by?: number;
  create_time?: string;
  update_time?: string;
  actions?: string[];
  creatorName?: string;
}

export interface OrchSopDetail {
  id: number;
  name: string;
  purpose?: null | string;
  status: number;
  config: {
    name?: string;
    steps?: Array<{
      subject: string;
      description?: string;
      max_attempts?: number;
      executor?: OrchMemberSpec & { mounts?: OrchMounts };
      verifier?: null | (OrchMemberSpec & { mounts?: OrchMounts });
    }>;
  };
}

export interface OrchSopSaveReq {
  name: string;
  purpose?: null | string;
  steps: OrchSopStepSpec[];
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
}) => defHttp.get<{ items: OrchSopItem[]; total: number }>(`${BASE_URL}/page`, { params });

export const GetDetail = (id: number) =>
  defHttp.get<OrchSopDetail>(`${BASE_URL}/${id}/detail`);

export const AddObj = (data: OrchSopSaveReq) =>
  defHttp.post<{ id: number }>(`${BASE_URL}/create`, data);

export const UpdateObj = (id: number, data: OrchSopSaveReq) =>
  defHttp.put(`${BASE_URL}/${id}/modify`, data);

export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

/** 节点执行者/检查者挂载候选（use 口径：自建 ∪ 授权） */
export const GetUsableResources = (kind: 'kb' | 'mcp' | 'skill' | 'tool') =>
  defHttp.get<{ items: ResourceOption[]; total: number }>(
    `${BASE_URL}/usable-resources/${kind}`,
  );

/** 克隆来源候选：单智能体列表（当前用户可见，走 SOP 侧 clone-sources，仅需 sop:list） */
export const ListAgentsForClone = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>(`${BASE_URL}/clone-sources`);

/** 单智能体详情（克隆时预填节点执行者/检查者 name/systemPrompt/mounts 用） */
export const GetAgentDetail = (id: number) =>
  defHttp.get<any>(`${BASE_URL}/clone-source/${id}`);

// ==================== 模型候选（表单抽屉的模型 picker） ====================

/** 本人已授权且支持工具调用的模型清单 */
export const ListModels = () =>
  defHttp.get<{ items: ResourceOption[]; total: number }>('/api/harness/models');
