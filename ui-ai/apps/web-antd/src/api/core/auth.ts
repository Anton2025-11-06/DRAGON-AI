import { baseRequestClient, requestClient } from '#/api/request';

export namespace AuthApi {
  /** 登录接口参数 */
  export interface LoginParams {
    password?: string;
    username?: string;
  }

  /** 登录用户信息（后端登录载荷） */
  export interface LoginUser {
    user_id: number;
    username: string;
    real_name: string;
    dept_id: number;
    roles: string[];
    permissions: string[];
    data_scopes: number[];
    data_scope_dept_ids?: number[];
  }

  /** 登录接口返回值 */
  export interface LoginResult {
    token: string;
    user?: LoginUser;
    expire?: number;
  }

  /** 注册接口参数 */
  export interface RegisterParams {
    username: string;
    password: string;
    confirm_password: string;
    email?: string;
    phone?: string;
    real_name?: string;
  }
}

/**
 * 登录（POST /api/login/login，网关转发 service_login）
 */
export async function loginApi(data: AuthApi.LoginParams) {
  return requestClient.post<AuthApi.LoginResult>('/api/login/login', data);
}

/**
 * 用户注册（POST /api/login/register）
 */
export async function registerApi(data: AuthApi.RegisterParams) {
  return requestClient.post('/api/login/register', data);
}

/**
 * 刷新Token（POST /api/login/refresh，携带 Authorization 头）
 */
export async function refreshTokenApi() {
  return baseRequestClient.post<{
    code: number;
    data: AuthApi.LoginResult;
    message: string;
  }>('/api/login/refresh');
}

/**
 * 退出登录（POST /api/login/logout）
 */
export async function logoutApi() {
  return requestClient.post('/api/login/logout');
}

/**
 * 获取用户权限码
 * 后端无独立权限接口，从 /api/system/users/me/info 的 permissions 字段提取
 */
export async function getAccessCodesApi() {
  const data = await requestClient.get<{ permissions: string[] }>(
    '/api/system/users/me/info',
  );
  return data.permissions ?? [];
}
