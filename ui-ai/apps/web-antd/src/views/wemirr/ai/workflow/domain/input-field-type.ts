/**
 * 开始节点输入字段的类型归一与展示元信息。
 *
 * BUG6：
 * - 短文本(SHORT_TEXT) 与 长文本(PARAGRAPH) 合并为统一的「文本」(TEXT)；
 * - 复选框(CHECKBOX) 更名为「开关」，控件改用 switch（存储值保持不变以兼容旧图）；
 * - 多文件的「最多文件数量」由 getMaxFileCount 统一给出上限，供表单渲染与上传校验复用。
 *
 * 旧工作流里已保存的 SHORT_TEXT / PARAGRAPH 在编辑与预览渲染时统一按 TEXT 处理。
 */
import type { InputField, InputFieldType } from '#/api/ai-workflow/types';

/** 归一后的字段类型（画布配置表单与动态输入表单实际使用的类型集合） */
export type NormalizedInputFieldType = Extract<
  InputFieldType,
  | 'APPROVER'
  | 'CHECKBOX'
  | 'FILE_LIST'
  | 'NUMBER'
  | 'SELECT'
  | 'SINGLE_FILE'
  | 'TEXT'
>;

/** 归一后的类型全集（同时作为未知类型的兜底依据） */
export const NORMALIZED_INPUT_FIELD_TYPES: NormalizedInputFieldType[] = [
  'TEXT',
  'NUMBER',
  'SELECT',
  'CHECKBOX',
  'SINGLE_FILE',
  'FILE_LIST',
  // 审批人入参：仅在画布存在 APPROVAL 节点时有意义（元素为数字或字符串）
  'APPROVER',
];

/** 新增字段时的默认类型 */
export const DEFAULT_INPUT_FIELD_TYPE: NormalizedInputFieldType = 'TEXT';

/** 多文件未配置上限时的默认最多文件数量 */
export const DEFAULT_MAX_FILE_COUNT = 5;

/** 文本字段未配置上限时的默认最大长度 */
export const DEFAULT_TEXT_MAX_LENGTH = 100_000;

const INPUT_FIELD_TYPE_LABELS: Record<NormalizedInputFieldType, string> = {
  TEXT: '文本',
  NUMBER: '数字',
  SELECT: '下拉选择',
  CHECKBOX: '开关',
  SINGLE_FILE: '单文件',
  FILE_LIST: '多文件',
  APPROVER: '审批人',
};

const INPUT_FIELD_TYPE_COLORS: Record<NormalizedInputFieldType, string> = {
  TEXT: 'blue',
  NUMBER: 'green',
  SELECT: 'purple',
  CHECKBOX: 'orange',
  SINGLE_FILE: 'magenta',
  FILE_LIST: 'red',
  APPROVER: 'cyan',
};

/**
 * 归一字段类型：历史文本类型（短文本/长文本）统一映射为 TEXT，
 * 空值或未知类型回落到默认类型，保证旧图不会渲染出空白控件。
 */
export function normalizeInputFieldType(
  type?: null | string,
): NormalizedInputFieldType {
  if (type === 'PARAGRAPH' || type === 'SHORT_TEXT') return 'TEXT';
  const matched = type as NormalizedInputFieldType;
  if (type && NORMALIZED_INPUT_FIELD_TYPES.includes(matched)) {
    return matched;
  }
  return DEFAULT_INPUT_FIELD_TYPE;
}

/** 是否为数组形态的字段（审批人：值永远是数组，元素可为数字或字符串） */
export function isArrayInputType(type?: null | string): boolean {
  return normalizeInputFieldType(type) === 'APPROVER';
}

/** 是否为（归一后的）文本类型 */
export function isTextInputType(type?: null | string): boolean {
  return normalizeInputFieldType(type) === 'TEXT';
}

/** 字段类型展示名 */
export function getInputFieldTypeLabel(type?: null | string): string {
  return INPUT_FIELD_TYPE_LABELS[normalizeInputFieldType(type)];
}

/** 字段类型标签颜色 */
export function getInputFieldTypeColor(type?: null | string): string {
  return INPUT_FIELD_TYPE_COLORS[normalizeInputFieldType(type)];
}

/** 多文件字段的最大文件数量（未配置时给默认上限） */
export function getMaxFileCount(field?: Partial<InputField>): number {
  const count = Number(field?.maxFileCount);
  return Number.isFinite(count) && count > 0 ? count : DEFAULT_MAX_FILE_COUNT;
}

/** 文本字段的最大长度（未配置时给默认上限） */
export function getTextMaxLength(field?: Partial<InputField>): number {
  const length = Number(field?.maxLength);
  return Number.isFinite(length) && length > 0
    ? length
    : DEFAULT_TEXT_MAX_LENGTH;
}
