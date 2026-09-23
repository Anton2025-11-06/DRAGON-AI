/**
 * 对话窗口输出渲染的分类工具。
 *
 * 工作流最终 outputs 的取值来自 END/REPLY 节点，节点输出又覆盖全部节点类型与
 * 12 种模型能力类型（文本、向量、重排分、图片/视频/音频直链、结构化 JSON、
 * 文件对象数组…），形态完全不固定。这里把「任意值」归一成有限的几种渲染形态，
 * 避免每加一种节点/模型类型就要改一次对话窗口。
 */

/** 对象里承载正文的常见键名（模型节点会把同一个值重复写到多个键上） */
export const TEXT_VALUE_KEYS = [
  'answer',
  'content',
  'message',
  'output',
  'result',
  'text',
];

/**
 * 递归收集值里的媒体直链（限深 2 层）。
 * 够覆盖 {urls: [...]}、{data: [{url}]} 这类包装，又不会把整份业务 JSON
 * 里的外部链接都当成产物铺成播放器。
 */
export function collectMediaUrls(value: unknown, depth = 0): string[] {
  if (depth > 2) return [];
  if (typeof value === 'string')
    return getMediaType(value) ? [value.trim()] : [];
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectMediaUrls(item, depth + 1));
  }
  if (value && typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).flatMap((item) =>
      collectMediaUrls(item, depth + 1),
    );
  }
  return [];
}

/** 值最终采用的渲染形态 */
export type ChatOutputKind =
  | 'audio'
  | 'fileList'
  | 'image'
  | 'json'
  | 'scalar'
  | 'text'
  | 'video';

const IMAGE_EXT = /\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff)(?:[?#]|$)/i;
const VIDEO_EXT = /\.(?:mp4|mov|webm|avi|mkv|m4v)(?:[?#]|$)/i;
const AUDIO_EXT = /\.(?:mp3|wav|m4a|aac|ogg|flac|amr|opus)(?:[?#]|$)/i;

/** 数值数组超过该长度就当作向量/打分集合，只给摘要不逐个铺开 */
export const VECTOR_LIKE_MIN_LENGTH = 16;

/** 是否为 http(s)/data 形态的 URL */
export function isUrlLike(text: unknown): text is string {
  if (typeof text !== 'string') return false;
  const value = text.trim();
  return (
    /^https?:\/\/\S+$/i.test(value) ||
    /^data:(?:image|audio|video)\/[^;]+;base64,.+$/is.test(value)
  );
}

/** URL（或 data URI）对应的媒体类型，非媒体返回 null */
export function getMediaType(url: unknown): 'audio' | 'image' | 'video' | null {
  if (!isUrlLike(url)) return null;
  const value = (url as string).trim();
  if (value.startsWith('data:image/')) return 'image';
  if (value.startsWith('data:audio/')) return 'audio';
  if (value.startsWith('data:video/')) return 'video';
  if (IMAGE_EXT.test(value)) return 'image';
  if (VIDEO_EXT.test(value)) return 'video';
  if (AUDIO_EXT.test(value)) return 'audio';
  return null;
}

/** 字符串是否形如 JSON 对象/数组（可解析才算，纯文本不强行当 JSON 渲染） */
export function parseJsonText(text: unknown): null | { data: any } {
  if (typeof text !== 'string') return null;
  const value = text.trim();
  if (!value || (!value.startsWith('{') && !value.startsWith('['))) {
    return null;
  }
  try {
    return { data: JSON.parse(value) };
  } catch {
    return null;
  }
}

/** 上传接口/开始节点文件变量的对象形态：{url, fileName, name, size} */
export function asFileObject(
  value: unknown,
): null | { name: string; url: string } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const url = (value as { url?: unknown }).url;
  if (!isUrlLike(url)) return null;
  const name = (value as { name?: unknown }).name;
  return {
    url: (url as string).trim(),
    name:
      typeof name === 'string' && name ? name : (url as string).slice(0, 40),
  };
}

/** 纯数值数组（向量 / 重排分） */
export function asNumberArray(value: unknown): null | number[] {
  return Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === 'number')
    ? (value as number[])
    : null;
}

/** 纯 URL 数组（文生图 urls、文件数组等） */
export function asUrlArray(value: unknown): null | string[] {
  return Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => isUrlLike(item))
    ? (value as string[]).map((item) => item.trim())
    : null;
}

/** 文件对象数组（开始节点多文件参数） */
export function asFileArray(
  value: unknown,
): null | { name: string; url: string }[] {
  if (!Array.isArray(value) || value.length === 0) return null;
  const files = value.map((item) => asFileObject(item));
  return files.every((item) => item !== null)
    ? (files as { name: string; url: string }[])
    : null;
}

/** 键值对数量少、值都可单独渲染的对象：按行展示比 JSON 树更易读 */
export function asFlatObject(
  value: unknown,
  maxEntries = 6,
): [string, any][] | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, any>);
  if (entries.length === 0 || entries.length > maxEntries) return null;
  return entries;
}

/** 耗时格式化（0 也正常显示，不折叠成空） */
export function formatDuration(ms?: number): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  return `${(ms / 60_000).toFixed(2)}min`;
}

/**
 * 工具来源文案（与后端 ai_nodes 的 TOOL_KIND_* 逐字对齐）。
 * 子工作流不写成「工作流」：在 LLM 节点的调用过程里它是被当工具调的那一条，
 * 写成同名容易让人误以为是图里另一个独立节点。
 */
export function toolKindText(kind?: string): string {
  if (kind === 'MCP') return 'MCP';
  if (kind === 'WORKFLOW') return '子工作流';
  if (kind === 'TOOL') return '工具';
  return kind || '未知来源';
}

/** 一次工具调用的状态标签：没收到结果帧就是「调用中」，不能默认成成功 */
export function toolCallState(
  call: { error?: null | string; result?: string },
  resumed?: boolean,
): { color: string; text: string } {
  if (call.error) return { color: 'red', text: '失败' };
  if (call.result === undefined) {
    return { color: 'processing', text: '调用中' };
  }
  return {
    color: resumed ? 'gold' : 'green',
    text: resumed ? '审批后补记' : '已返回',
  };
}

/**
 * 任意值的可复制文本：字符串原样给（正文就是要抄走的内容），
 * 其余走 JSON 序列化，保证复制结果和屏幕上看到的是同一份数据。
 */
export function toCopyText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** 写剪贴板（非安全上下文下 clipboard 不可用，退回 execCommand） */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 页面不是 https / 用户拒绝了权限时走兜底
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}
