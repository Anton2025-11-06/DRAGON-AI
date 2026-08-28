import { defHttp } from '#/api/request';

const BASE_URL = '/api/system/users';

/** 用户列表（GET /api/system/users?page=&page_size=&username=&status=） */
export function GetList(query: any) {
  return defHttp.get(BASE_URL, { params: query });
}

/** 新增用户（POST /api/system/users） */
export function AddObj(obj: any) {
  return defHttp.post(BASE_URL, obj);
}

/** 更新用户（PUT /api/system/users/{user_id}） */
export function UpdateObj(obj: any) {
  return defHttp.put(`${BASE_URL}/${obj.user_id}`, obj);
}

/** 删除用户（DELETE /api/system/users/{user_id}） */
export function DelObj(id: string) {
  return defHttp.delete(`${BASE_URL}/${id}`);
}

/** 管理员重置密码（PUT /api/system/users/{user_id}/password） */
export function ResetPassword(id: string, password: string) {
  return defHttp.put(`${BASE_URL}/${id}/password`, { password });
}

/** 查询用户角色（GET /api/system/users/{user_id}/roles） */
export function GetUserRoles(id: string) {
  return defHttp.get(`${BASE_URL}/${id}/roles`);
}

/** 分配用户角色（PUT /api/system/users/{user_id}/roles） */
export function AssignRoles(id: string, role_ids: number[]) {
  return defHttp.put(`${BASE_URL}/${id}/roles`, { role_ids });
}