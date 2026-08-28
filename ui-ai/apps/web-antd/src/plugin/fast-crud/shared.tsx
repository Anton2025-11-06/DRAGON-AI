/**
 * fast-crud 全局共享配置
 * 抽取重复使用的字典、列配置和工具函数
 */
import type { ValueBuilderContext } from '@fast-crud/fast-crud';

import { dict, utils } from '@fast-crud/fast-crud';
import dayjs from 'dayjs';

// ==================== 字典配置 ====================

/** 布尔值字典 - 是/否 */
export const booleanDict = () =>
  dict({
    data: [
      { value: true, label: '是', color: 'success' },
      { value: false, label: '否', color: 'error' },
    ],
  });

/** 状态字典 - 启用/禁用 */
export const statusDict = () =>
  dict({
    data: [
      { value: true, label: '启用', color: 'success' },
      { value: false, label: '禁用', color: 'error' },
    ],
  });

/** 查询条件字典 - 精准/模糊 */
export const searchConditionDict = () =>
  dict({
    data: [
      { value: 'EQ', label: '精准', color: 'success' },
      { value: 'Like', label: '模糊', color: 'error' },
    ],
  });

/** HTTP 方法字典 */
export const httpMethodDict = () =>
  dict({
    data: [
      { label: 'ALL', value: 'ALL', color: 'success' },
      { label: 'GET', value: 'GET', color: 'success' },
      { label: 'POST', value: 'POST', color: 'success' },
      { label: 'PUT', value: 'PUT', color: 'success' },
      { label: 'DELETE', value: 'DELETE', color: 'error' },
      { label: 'PATCH', value: 'PATCH', color: 'success' },
    ],
  });

// ==================== 值构建器 ====================

/** 日期时间值构建器 - 将日期字符串转换为 dayjs 对象 */
export function datetimeValueBuilder({
  value,
  row,
  key,
}: ValueBuilderContext): void {
  if (value !== null && value !== undefined) {
    row[key] = dayjs(value);
  }
}

// ==================== 通用列配置 ====================

/** 隐藏 ID 列 */
export const hiddenIdColumn = {
  title: 'ID',
  type: 'text',
  form: { show: false },
  column: { show: false },
} as const;

/** 创建时间列 */
export const createTimeColumn = {
  title: '创建时间',
  type: 'datetime',
  column: { width: 180 },
  form: { show: false },
  valueBuilder: datetimeValueBuilder,
} as const;

/** 创建时间列（只读样式） */
export const createTimeReadonlyColumn = {
  title: '创建时间',
  type: 'datetime',
  column: { width: 180 },
  form: { show: false },
  // viewForm: { show: true },
  valueBuilder: datetimeValueBuilder,
};

/** 描述字段列 */
export const descriptionColumn = {
  title: '描述',
  type: 'textarea',
  search: { show: false },
  form: { col: { span: 24 } },
  column: { ellipsis: true },
} as const;

/** 时间范围字段配置 */
export const datetimeRangeColumn = {
  title: '限时范围',
  type: 'datetimerange',
  valueBuilder({ row, key }: ValueBuilderContext): void {
    if (!utils.strings.hasEmpty(row.startTime, row.endTime)) {
      row[key] = [dayjs(row.startTime), dayjs(row.endTime)];
    }
  },
  valueResolve({ form, key }: any) {
    if (form[key] !== null && !utils.strings.hasEmpty(form[key])) {
      form.startTime = dayjs(form[key][0]).format();
      form.endTime = dayjs(form[key][1]).format();
    } else {
      form.startTime = null;
      form.endTime = null;
    }
  },
} as const;

// ==================== 工具函数 ====================

/** 表单值重置工具 */
export function resetFormValues<T extends Record<string, any>>(
  target: T,
  source: T,
): void {
  Object.keys(source).forEach((key) => {
    target[key as keyof T] = source[key as keyof T];
  });
}
