import { defHttp } from '#/api/request';

/**
 * 资源实例授权（ACL）前端接口层。
 *
 * 两条口径必须和后端保持一致，这里注释说明原因：
 * 1. 列表页行内按钮只认后端下发的 `actions`（资源列表接口按 ACL 求值算好的），
 *    前端不复制判定规则 —— 否则后端加了「同部门可用」这类档位，前端还是老判断，
 *    就会出现按钮亮着但点了 403（或反过来该亮的没亮）。
 * 2. 动作码/主体类型的中文名一律从 /acl/resources 取，不在前端硬编码第二份清单。
 */
const BASE_URL = '/api/system/acl';

/** 授权主体类型：值同后端 resource_guard.GRANTEE_*，名称由 /acl/resources 下发 */
export const GRANTEE_TYPE = {
  ALL: 5,
  DEPT: 3,
  GROUP: 4,
  ROLE: 2,
  USER: 1,
} as const;

/**
 * 动作码别名：后端「启用/停用」与「编辑」是同一个码（不另开 toggle，避免两码一义），
 * 但页面按钮习惯写 toggle，这里做一次映射，前端调用方不必知道这个细节。
 */
const ACTION_ALIASES: Record<string, string> = {
  toggle: 'edit',
};

/** 带 ACL 求值结果的列表行（各资源页的 row 都有这个字段） */
export interface AclActionableRow {
  actions?: string[];
  [key: string]: any;
}

/** 动作码元信息（/acl/resources 下发，弹窗按它渲染勾选框，前端不再抄一份清单） */
export interface AclActionMeta {
  code: string;
  name: string;
}

/**
 * 「操作范围」的一组（后端 RESOURCE_SPECS 里每个资源自己写的 groups）。
 * 分组是鉴权语义的一部分（哪个动作算库内文件、哪个算引用），所以放后端下发；
 * 图标与解释句是纯展示，留在前端 acl-action-meta。
 */
export interface AclActionGroupMeta {
  actions: AclActionMeta[];
  hint: string;
  key: string;
  name: string;
}

/** 主体类型元信息：同一份 {code,name} 形状，但 code 是数字 */
export interface AclOptionMeta {
  code: number;
  name: string;
}

export interface AclResourceMeta {
  grant_permission: string;
  grantee_types: AclOptionMeta[];
  resource_code: string;
  resource_name: string;
  actions: AclActionMeta[];
  scope_actions: AclActionMeta[];
  /** 分组清单（旧版后端不下发，前端退成本地兜底的两组） */
  groups?: AclActionGroupMeta[];
}

/** 授权主体候选（/acl/grantees） */
export interface AclGranteeOption {
  grantee_id: number;
  grantee_name: string;
  label: string;
}

/** 在册授权里的单个动作（后端按「主体 × 动作」一行一行存） */
export interface AclGrantActionRow {
  acl_id: number;
  action: string;
  action_name: string;
}

/** 在册授权：同主体的多个动作已合并成一条 */
export interface AclGrantItem {
  dept_include_sub: number;
  expire_time: null | string;
  grantee_id: number;
  grantee_name: string;
  grantee_type: number;
  grantee_type_name: string;
  actions: AclGrantActionRow[];
}

/** 提交用的授权行：一个主体一组动作 + 一个过期时间 */
export interface AclGrantPayload {
  actions: string[];
  dept_include_sub?: number;
  expire_time?: null | string;
  grantee_id: number;
  grantee_type: number;
}

/** 被跳过的行（如给归属人自己授权）：不是错误，弹窗要能原样摊给人看 */
export interface AclSkippedRow {
  grantee_id: number;
  grantee_type: number;
  reason: string;
}

export interface AclResourceBrief {
  kb_id: number;
  owner_id: number;
  resource_code: string;
  resource_id: number;
  resource_name: string;
  title: string;
}

export interface AclGrantListResult {
  items: AclGrantItem[];
  resource: AclResourceBrief;
}

export interface AclSaveResult {
  revoked: number;
  saved: number;
  skipped: AclSkippedRow[];
  resource: AclResourceBrief;
}

/**
 * 当前用户对这一行能不能做某个动作。
 *
 * 唯一实现方式：读后端下发的 actions。传字符串数组也兼容（有些调用方手里只有
 * 求值结果，没有整行），所以入参放宽。
 */
export function canAction(
  row: AclActionableRow | string[] | null | undefined,
  action: string,
): boolean {
  if (!row || !action) return false;
  const list = Array.isArray(row) ? row : (row.actions ?? []);
  return list.includes(ACTION_ALIASES[action] ?? action);
}

/** 资源类型的可授权动作清单（resource_code 留空 = 全部已登记类型） */
export function GetResourceMeta(resourceCode?: string) {
  return defHttp.get<AclResourceMeta[]>(`${BASE_URL}/resources`, {
    params: resourceCode ? { resource_code: resourceCode } : {},
  });
}

/** 授权主体候选（全员类型不用检索，后端固定返回一条） */
export function GetGranteeOptions(params: {
  grantee_type: number;
  keyword?: string;
  limit?: number;
}) {
  return defHttp.get<AclGranteeOption[]>(`${BASE_URL}/grantees`, { params });
}

/** 某条资源的在册授权（无 share 时后端 403，弹窗据此进错误态） */
export function ListGrants(resourceCode: string, resourceId: number | string) {
  return defHttp.get<AclGrantListResult>(`${BASE_URL}/grants`, {
    params: { resource_code: resourceCode, resource_id: resourceId },
  });
}

/**
 * 整表提交授权：grants 就是这条资源授权的目标全集，空数组 = 清空。
 *
 * expire_time 传 'YYYY-MM-DDTHH:mm:ss'（不带时区偏移）：后端拿它和 naive 的
 * datetime.now() 比较，带上 +08:00 会变成 aware 时间，比较直接 TypeError。
 */
export function SaveGrants(data: {
  grants: AclGrantPayload[];
  resource_code: string;
  resource_id: number | string;
}) {
  return defHttp.put<AclSaveResult>(`${BASE_URL}/grants`, data);
}

/** 追加授权（不动在册行）：批量补授权时用，免为先拉全集再整表回传 */
export function AppendGrants(data: {
  grants: AclGrantPayload[];
  resource_code: string;
  resource_id: number | string;
}) {
  return defHttp.post<AclSaveResult>(`${BASE_URL}/grants`, data);
}

/** 撤销某个主体的授权：actions 留空 = 撤销该主体在这条资源上的全部动作 */
export function RevokeGrants(params: {
  actions?: string;
  grantee_id?: number;
  grantee_type: number;
  resource_code: string;
  resource_id: number | string;
}) {
  return defHttp.delete<{ revoked: number }>(`${BASE_URL}/grants`, { params });
}
