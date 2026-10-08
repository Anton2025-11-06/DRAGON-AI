import { defHttp } from '#/api/request';

const BASE_URL = '/api/system/user-groups';

/**
 * 用户组接口。
 *
 * 定位与后端一致：用户组只是「资源 ACL 的一类授权主体」，本页面只管组织事实
 * （组名、启停、成员）；给组授什么权限在资源列表页的授权弹窗里做。
 */

/** 列表行：GET /api/system/user-groups → {total, items} */
export interface UserGroupItem {
  create_time?: string;
  created_by?: number;
  description?: string;
  group_id: number;
  group_name: string;
  /** 后端一次 group by 批量算好，列表直接展示 */
  member_count: number;
  status: number;
  update_time?: string;
}

/** 组成员行：GET /api/system/user-groups/{group_id}/members */
export interface UserGroupMemberItem {
  dept_id: number;
  dept_name?: null | string;
  real_name?: null | string;
  status: number;
  user_id: number;
  username: string;
}

/** 成员候选：GET /api/system/user-groups/member-candidates（按数据范围过滤） */
export interface UserCandidate {
  label: string;
  real_name: string;
  user_id: number;
}

/** 分页查询（page / page_size 由 fast-crud 的 transformQuery 统一给出） */
export function GetList(query: any) {
  return defHttp.get(BASE_URL, { params: query });
}

/** 新增（POST） */
export function AddObj(obj: any) {
  return defHttp.post(BASE_URL, obj);
}

/** 更新（PUT /{group_id}） */
export function UpdateObj(obj: any) {
  return defHttp.put(`${BASE_URL}/${obj.group_id}`, obj);
}

/** 删除（DELETE /{group_id}）：后端会连带撤销指向该组的授权行 */
export function DelObj(id: number | string) {
  return defHttp.delete(`${BASE_URL}/${id}`);
}

/** 在册成员 */
export function GetMembers(groupId: number | string) {
  return defHttp.get<UserGroupMemberItem[]>(`${BASE_URL}/${groupId}/members`);
}

/**
 * 整表重设成员（语义与「角色分配菜单」一致：先清后插，提交的就是最终状态）。
 *
 * 注意：成员变化不即时作用于已登录用户 —— group_ids 在登录时算好写进 token 载荷，
 * 与角色/部门变化同口径（下次登录生效），所以弹窗保存后要把这句话讲给人听。
 */
export function AssignMembers(groupId: number | string, user_ids: number[]) {
  return defHttp.put<{ member_count: number }>(
    `${BASE_URL}/${groupId}/members`,
    { user_ids },
  );
}

/** 成员候选用户：不要求 system:user:list，有成员维护权就能选人 */
export function GetMemberCandidates(keyword?: string) {
  return defHttp.get<UserCandidate[]>(`${BASE_URL}/member-candidates`, {
    params: { keyword: keyword || undefined, limit: 50 },
  });
}
