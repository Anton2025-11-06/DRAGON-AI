import type { McpToolInfo } from './api';

/** MCP 工具的一个入参（JSON Schema properties 摊平后的行） */
export interface McpParamRow {
  desc: string;
  enumValues?: string[];
  /** 参数名，同时作为调用 arguments 的键 */
  name: string;
  /** 是否必填：顶层 required 数组或字段级 required 任一命中即为真 */
  required: boolean;
  type: string;
}

/** object/array 之外的类型都能用普通输入框表达 */
export const JSON_VALUE_TYPES = ['array', 'object'];

/**
 * 工具列表里的「类型」列展示值：Schema 常用 anyOf 表达可空类型，取第一个非 null。
 */
function paramType(prop: any): string {
  const direct = prop?.type;
  if (typeof direct === 'string' && direct) return direct;
  const union = [...(prop?.anyOf || []), ...(prop?.oneOf || [])]
    .map((item: any) => item?.type)
    .filter((item: string) => item && item !== 'null');
  if (union.length > 0) return union.join('/');
  if (Array.isArray(prop?.enum) && prop.enum.length > 0) return 'enum';
  if (prop?.properties) return 'object';
  if (prop?.items) return 'array';
  return 'any';
}

/**
 * JSON Schema → 参数行数组。
 *
 * 必填有两种写法都要认：标准是顶层 `required: ["a","b"]`，但也有服务端直接把
 * `required: true` 标在字段上；只看前者会让这批工具的必填列整片空白（页面看不出区别）。
 *
 * @returns 无 properties 时返回 null（调用方据此收起参数区，区别于「有参数但 0 必填」）
 */
export function schemaParams(
  schema?: null | Record<string, any>,
): McpParamRow[] | null {
  const props = schema?.properties;
  if (!props || typeof props !== 'object' || Array.isArray(props)) return null;
  const requiredList = Array.isArray(schema?.required) ? schema.required : [];
  return Object.entries(props).map(([name, prop]: [string, any]) => ({
    desc: prop?.description || prop?.title || '',
    enumValues: Array.isArray(prop?.enum) ? prop.enum.map(String) : undefined,
    name,
    required: requiredList.includes(name) || prop?.required === true,
    type: paramType(prop),
  }));
}

/** 工具参数行数摘要，如「3 个参数 · 2 必填」 */
export function paramsSummary(rows: McpParamRow[] | null): string {
  if (!rows) return '';
  const required = rows.filter((row) => row.required).length;
  return required > 0
    ? `${rows.length} 个参数 · ${required} 必填`
    : `${rows.length} 个参数`;
}

/** 取某个工具的参数行 */
export function toolParams(tool?: McpToolInfo | null): McpParamRow[] | null {
  return schemaParams(tool?.inputSchema);
}
