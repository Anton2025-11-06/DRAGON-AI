import { defHttp } from '#/api/request';

// ==================== 类型定义 ====================

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
}

/** 沙箱对话响应 */
export interface SandboxChatRep {
  reply: string;
  sandbox: string;
  skills: string[];
  tools: string[];
  kbs: string[];
}

/** 技能 */
export interface SandboxSkill {
  id: string;
  name: string;
  description: string;
}

/** MCP 工具（用于挂载） */
export interface McpToolOption {
  id: number;
  name: string;
  url: string;
}

/** 知识库（用于挂载） */
export interface KbOption {
  kb_id: number;
  kb_name: string;
  description: string;
}

// ==================== API 接口 ====================

/** 沙箱工作区目录列表 */
export function SandboxFiles(path = '') {
  return defHttp.get<SandboxFilesRep>('/api/workflow/sandbox/files', { path });
}

/** 查看沙箱文本文件 */
export function SandboxFile(path: string) {
  return defHttp.get<SandboxFileRep>('/api/workflow/sandbox/file', { path });
}

/** 上传文件到当前目录（path 通过 FormData 传递） */
export function SandboxUpload(path: string, file: File) {
  return defHttp.upload<{ name: string; size: number }>(
    '/api/workflow/sandbox/upload',
    {
      path,
      file,
    },
  );
}

/** 沙箱下载地址（文件或文件夹 zip） */
export function SandboxDownloadUrl(path: string) {
  return `/api/workflow/sandbox/download?path=${encodeURIComponent(path)}`;
}

/** 对话沙箱 AI */
export function SandboxChat(
  message: string,
  skills: string[],
  tools: string[],
  kbs: string[],
) {
  return defHttp.post<SandboxChatRep>('/api/workflow/sandbox/chat', {
    message,
    skills,
    tools,
    kbs,
  });
}

/** 沙箱技能列表 */
export function SandboxSkills() {
  return defHttp.get<{ items: SandboxSkill[]; total: number }>(
    '/api/workflow/sandbox/skills',
  );
}

/** MCP 连接分页（工具选择） */
export function McpServersPage(params: { current: number; size: number }) {
  return defHttp.post<{ records: McpToolOption[]; total: number }>(
    '/api/workflow/mcp-server/page',
    params,
  );
}

/** 知识库列表（挂载知识库） */
export function KbList(params: { page: number; page_size: number }) {
  return defHttp.get<{ items: KbOption[]; total: number }>(
    '/api/rag/kb',
    params,
  );
}
