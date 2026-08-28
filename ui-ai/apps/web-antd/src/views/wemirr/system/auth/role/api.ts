import { defHttp } from '#/api/request';

const BASE_URL = '/api/system/roles';

/** 角色列表（GET /api/system/roles?page=&page_size=&role_name=） */
export function GetList(query: any) {
  return defHttp.get(BASE_URL, { params: query });
}

/** 新增角色（POST /api/system/roles） */
export function AddObj(obj: any) {
  return defHttp.post(BASE_URL, obj);
}

/** 更新角色（PUT /api/system/roles/{role_id}） */
export function UpdateObj(obj: any) {
  return defHttp.put(`${BASE_URL}/${obj.role_id}`, obj);
}

/** 删除角色（DELETE /api/system/roles/{role_id}） */
export function DelObj(id: string) {
  return defHttp.delete(`${BASE_URL}/${id}`);
}

/** 查询角色已分配的菜单（GET /api/system/roles/{role_id}/menus）→ [menu_id] */
export function getRoleMenus(roleId: number) {
  return defHttp.get(`${BASE_URL}/${roleId}/menus`);
}

/** 分配角色菜单权限（PUT /api/system/roles/{role_id}/menus） */
export function assignMenus(roleId: number, menu_ids: number[]) {
  return defHttp.put(`${BASE_URL}/${roleId}/menus`, { menu_ids });
}

// ==================== 数据权限范围 ====================
/** 数据权限范围枚举：1-全部 2-本部门及以下 3-本部门 4-仅本人 5-自定义部门 */
export const DataScopeEnum = {
  ALL: 1,
  DEPT_CHILDREN: 2,
  DEPT: 3,
  SELF: 4,
  CUSTOM: 5,
} as const;

/** 数据权限范围选项 */
export const dataScopeOptions = [
  { value: DataScopeEnum.ALL, label: '全部', color: 'success' },
  {
    value: DataScopeEnum.DEPT_CHILDREN,
    label: '本部门及以下',
    color: 'processing',
  },
  { value: DataScopeEnum.DEPT, label: '本部门', color: 'warning' },
  { value: DataScopeEnum.SELF, label: '仅本人', color: 'default' },
  { value: DataScopeEnum.CUSTOM, label: '自定义部门', color: 'error' },
];