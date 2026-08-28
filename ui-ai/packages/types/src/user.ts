import type { BasicUserInfo } from '@vben-core/typings';

/** 用户信息 */
interface UserInfo extends BasicUserInfo {
  /**
   * 用户描述
   */
  desc: string;
  /**
   * 首页地址
   */
  homePath: string;

  /**
   * accessToken
   */
  token: string;

  /**
   * 真实姓名（后端 real_name）
   */
  realName?: string;

  /**
   * 所属部门名称（后端 dept_name）
   */
  deptName?: string;

  /**
   * 权限码列表（后端 me/info 的 permissions 字段）
   */
  permissions?: string[];
}

export type { UserInfo };
