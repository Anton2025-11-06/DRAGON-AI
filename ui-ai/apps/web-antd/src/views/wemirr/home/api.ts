import { defHttp } from '#/api/request';

// ==================== 类型定义（沙箱 demo 旧口，M2 起逐步切 harness） ====================

/** 沙箱文件条目 */
export interface SandboxItem {
  name: string;
  type: 'dir' | 'file';
  size: number;
  mtime: string;
}

/** 沙箱目录列表响应 */
export interface SandboxFilesRep {
  workspace: string;
  current_path: string;
  parent_path: string;
  items: SandboxItem[];
}

/** 沙箱文本文件内容 */
export interface SandboxFileRep {
  path: string;
  name: string;
  size: number;
  truncated: boolean;
  content: string;
  /** harness 口：二进制文件标记（内容走下载口） */
  binary?: boolean;
}

// ==================== 类型定义（harness 深度探索） ====================

/** 四类能力挂载快照（会话级持久，每轮发送可覆盖） */
export interface HarnessMounts {
  kbIds: number[];
  mcpIds: number[];
  toolIds: number[];
  skillIds: number[];
}

/** 深度探索会话 */
export interface HarnessSession {
  id: number;
  title: string;
  /** 0 = 未选模型（发消息前必须选） */
  modelApplyId: number;
  modelName: string;
  mounts: Partial<HarnessMounts>;
  status: string;
  createTime: string;
  updateTime: string;
}

/** 工具调用轨迹（assistant 消息的 toolCalls 元素，与 worker 落库结构一致） */
export interface ToolCallTrace {
  tool: string;
  toolCallId?: string;
  arguments?: string;
}

/** 深度探索消息（一轮两行：USER + ASSISTANT，按 turnId 关联） */
export interface HarnessMessage {
  id: number;
  sessionId: number;
  turnId: string;
  role: 'ASSISTANT' | 'USER';
  content: string;
  thinking: null | string;
  toolCalls: ToolCallTrace[];
  status: 'COMPLETED' | 'FAILED' | 'INTERRUPTED' | 'RUNNING';
  error: null | string;
  modelName: null | string;
  createTime: string;
}

/** 可选模型（已按支持工具调用过滤；applyId>0 = 本人已授权可选） */
export interface HarnessModelOption {
  id: number;
  name: string;
  provider: string;
  modelName: string;
  category: string;
  applyId: number;
  applied: boolean;
}

/** SSE 协议帧（data: {json}；type 与 worker 侧 publish 的口径一致） */
export interface HarnessFrame {
  type:
    | 'thinking.delta'
    | 'text.delta'
    | 'tool.completed'
    | 'tool.started'
    | 'turn.completed'
    | 'turn.failed'
    | 'turn.interrupted'
    | 'turn.started';
  turnId: string;
  ts?: number;
  /** text.delta / thinking.delta / turn.completed 的正文 */
  content?: string;
  /** tool.started / tool.completed 的工具名 */
  tool?: string;
  toolCallId?: string;
  /** tool.completed 的调用入参 */
  arguments?: string;
  /** turn.failed 的错误信息 */
  error?: string;
  /** turn.interrupted 的中断原因 */
  reason?: string;
  /** HITL 挂起的问题文本（智能体在等答复；优先于 reason 显示） */
  question?: string;
}

// ==================== API 接口（harness 深度探索） ====================

/** 我的会话分页（按 updateTime 倒序：最近动过的排最前） */
export function HarnessSessionPage(params: { current: number; size: number }) {
  return defHttp.get<{
    current: number;
    records: HarnessSession[];
    size: number;
    total: number;
  }>('/api/harness/session/page', { params });
}

/** 新建会话（模型与初始挂载一起落，首轮即可发消息） */
export function HarnessSessionCreate(body: {
  title: string;
  modelApplyId: number;
  mounts?: Partial<HarnessMounts>;
}) {
  return defHttp.post<HarnessSession>('/api/harness/session', body);
}

/** 会话消息历史（按 id 平铺序：USER/ASSISTANT 交替，一轮两行） */
export function HarnessSessionMessages(sessionId: number) {
  return defHttp.get<HarnessMessage[]>(
    `/api/harness/session/${sessionId}/messages`,
  );
}

/** 编辑会话（只传要改的键：标题/模型/挂载）——封装层无 patch 快捷方法，走通用 request */
export function HarnessSessionUpdate(
  sessionId: number,
  body: {
    modelApplyId?: number;
    mounts?: Partial<HarnessMounts>;
    title?: string;
  },
) {
  return defHttp.request(`/api/harness/session/${sessionId}`, {
    data: body,
    method: 'PATCH',
  });
}

/** 删除会话（含全部消息） */
export function HarnessSessionDelete(sessionId: number) {
  return defHttp.delete(`/api/harness/session/${sessionId}`);
}

/** 可选模型清单（支持工具调用 + 本人授权标注） */
export function HarnessModels() {
  return defHttp.get<{ items: HarnessModelOption[]; total: number }>(
    '/api/harness/models',
  );
}

/** 发送一轮对话：建 turn → 投 arq harness 流水线 → 返回 turnId（随后订阅事件流） */
export function HarnessChat(body: {
  message: string;
  mounts?: Partial<HarnessMounts>;
  sessionId: number;
}) {
  return defHttp.post<{ turnId: string }>('/api/harness/chat', body);
}

/** 请求中断本轮（worker 在事件间隙检查退出，终态帧由服务端下发） */
export function HarnessInterrupt(turnId: string) {
  return defHttp.post(`/api/harness/chat/${turnId}/interrupt`);
}

/** SSE 订阅地址：网关按 /subscribe 后缀放行（turnId 为 uuid4 熵，无需登录头） */
export function HarnessSubscribeUrl(turnId: string) {
  return `/api/harness/chat/${turnId}/subscribe`;
}

// ==================== 沙箱文件（harness 口：每用户 OpenSandbox 工作区代理） ====================

/** 沙箱工作区目录列表 */
export function SandboxFiles(path = '') {
  return defHttp.get<SandboxFilesRep>('/api/harness/sandbox/files', { params: { path } });
}

/** 查看沙箱文件内容（binary=true 时 content 为占位说明） */
export function SandboxFile(path: string) {
  return defHttp.get<SandboxFileRep>('/api/harness/sandbox/file', { params: { path } });
}

/** 上传文件到当前目录（path 通过 FormData 传递，单次≤20MB） */
export function SandboxUpload(path: string, file: File) {
  return defHttp.upload<{ name: string; size: number }>(
    '/api/harness/sandbox/upload',
    {
      path,
      file,
    },
  );
}

/** 沙箱下载地址（文件原样 / 目录 zip） */
export function SandboxDownloadUrl(path: string) {
  return `/api/harness/sandbox/download?path=${encodeURIComponent(path)}`;
}

/** 重置我的沙箱（清空工作区文件；会话与消息不动） */
export function HarnessWorkspaceReset() {
  return defHttp.delete('/api/harness/sandbox');
}

// ==================== 挂载候选（harness options 四口，use 口径） ====================

/**
 * 挂载候选条目（/api/harness/options/{kind} 平铺返回）
 * （skill 口另带 code、mcp 口另带 type；口径与 worker 侧挂载校验同源）
 */
export interface HarnessOptionItem {
  id: number;
  name: string;
  description?: null | string;
  /** 技能标识（skill 口返回） */
  code?: string;
  /** MCP 连接类型（mcp 口返回：SSE/STDIO） */
  type?: string;
}

/** 四类能力挂载候选（use 口径：自建 ∪ ACL 授权；选得中即挂得上） */
export function HarnessOptions(kind: 'kb' | 'mcp' | 'skill' | 'tool') {
  return defHttp.get<{ items: HarnessOptionItem[]; total: number }>(
    `/api/harness/options/${kind}`,
  );
}
