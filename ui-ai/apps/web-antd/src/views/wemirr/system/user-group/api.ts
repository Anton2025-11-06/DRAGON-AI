import { defHttp } from '#/api/request';

const BASE_URL = '/api/system/user-groups';

/**
 * 用户组接口。
 *
 * 定位与后端一致：用户组只是「资源 ACL 的一类授权主体」，本页面只管组织事实
 * （组名、启停、成员）；给组授什么权限在资源列表页的授权弹窗里做。
 *
 * 可见范围与后端同口径：列表只回自己创建的组，改/删/成员四个口后端还卡归属
 * （ADMIN 除外）。所以进到这一页的操作都是在自己建的组上，不要拿
 * group_id 去碰别人的组 —— 只会收到一句「只能操作自己创建的用户组」。
 */

/** 列表行：GET /api/system/user-groups → {total, items} */
export interface UserGroupItem {
  create_time?: string;
  created_by?: number;
  /**
   * 创建人姓名：后端一次批量回表取好（组名不再全局唯一，靠这一列分清同名组）。
   * 历史回填的 created_by=0、或创建人已被删时为 null，页面显示占位符。
   */
  creator_name?: null | string;
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

/**
 * 成员候选：GET /api/system/user-groups/member-candidates（除管理员账号外不做筛人）
 *
 * 后端逐列下发 姓名/账号/部门，并且已经按调用者身份把系统管理员账号藏掉了
 * （非 ADMIN 搜不到也选不到），所以这里不要再拿 label 回拆字段。
 * 部门与数据范围不参与筛选：建组的协作对象常在别的部门。
 */
export interface UserCandidate {
  dept_name?: null | string;
  label: string;
  real_name?: null | string;
  user_id: number;
  username?: string;
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

/**
 * 成员候选用户：不要求 system:user:list，有成员维护权就能选人。
 *
 * 默认 100 条（后端上限也是 100）：开弹窗就给人一批可点的，0 条和接口坏了在界面上分不开。
 */
export function GetMemberCandidates(keyword?: string) {
  return defHttp.get<UserCandidate[]>(`${BASE_URL}/member-candidates`, {
    params: { keyword: keyword || undefined, limit: 100 },
  });
}
