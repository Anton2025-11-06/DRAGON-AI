/**
 * 对话窗口输出渲染的分类工具。
 *
 * 工作流最终 outputs 的取值来自 END/REPLY 节点，节点输出又覆盖全部节点类型与
 * 12 种模型能力类型（文本、向量、重排分、图片/视频/音频直链、结构化 JSON、
 * 文件对象数组…），形态完全不固定。这里把「任意值」归一成有限的几种渲染形态，
 * 避免每加一种节点/模型类型就要改一次对话窗口。
 */

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
