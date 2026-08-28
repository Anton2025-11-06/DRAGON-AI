import { defHttp } from '#/api/request';

export interface UserPasswordUpdateParams {
  old_password: string;
  new_password: string;
}

/**
 * 获取当前用户信息（GET /api/system/users/me/info）
 * 返回 { user, roles, role_names, permissions, menus }
 */
export async function getUserInfoApi() {
  return defHttp.get<Record<string, any>>('/api/system/users/me/info');
}

/**
 * 修改当前用户密码（PUT /api/system/users/{user_id}/own-password）
 * 仅允许修改本人密码，需校验原密码
 */
export async function updateUserPasswordApi(
  userId: number,
  data: UserPasswordUpdateParams,
) {
  return defHttp.put(`/api/system/users/${userId}/own-password`, data);
}