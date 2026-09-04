/**
 * 该文件可自行根据业务逻辑进行调整
 */
import type { RequestClientOptions } from '@vben/request';

import { useAppConfig } from '@vben/hooks';
import { preferences } from '@vben/preferences';
import {
  authenticateResponseInterceptor,
  defaultResponseInterceptor,
  errorMessageResponseInterceptor,
  RequestClient,
} from '@vben/request';
import { useAccessStore } from '@vben/stores';

import { useUi } from '@fast-crud/fast-crud';

import { useAuthStore } from '#/store';
import { generateUUID } from '#/utils/uuid';

import { refreshTokenApi } from './core';

const { apiURL } = useAppConfig(import.meta.env, import.meta.env.PROD);

// api 层已统一书写完整路径 /api/{service}/{path}，基础地址若以 /api 结尾则截断，
// 避免 axios 拼接出 /api/api/... 双重前缀（VITE_GLOB_API_URL=/api 时 requestBaseURL 为空，直接走相对网关路径）
const requestBaseURL = apiURL.replace(/\/+$/, '').replace(/\/api$/, '');

// ==================== 链路追踪 trace-id ====================
// 后端每次响应（含错误响应）都会携带 X-Trace-Id（网关 RequestLogMiddleware 生成/沿用）；
// 前端记录后，后续所有请求回带同一 trace-id，便于后台将同一用户的操作串联为一条链路。
// 本地持久化：刷新页面后继续沿用，直到后端更换该 ID。
const TRACE_ID_KEY = 'x-trace-id';

export function getTraceId(): string {
  return localStorage.getItem(TRACE_ID_KEY) || '';
}

export function saveTraceId(traceId: string) {
  if (traceId && getTraceId() !== traceId) {
    localStorage.setItem(TRACE_ID_KEY, traceId);
  }
}

function createRequestClient(baseURL: string, options?: RequestClientOptions) {
  const client = new RequestClient({
    ...options,
    baseURL,
  });

  // 重新认证防抖：避免多处 401/403 同时触发重复登出
  let reAuthenticating = false;

  /**
   * 重新认证逻辑
   */
  async function doReAuthenticate() {
    if (reAuthenticating) return;
    reAuthenticating = true;
    try {
      console.warn('Access token or refresh token is invalid or expired. ');
      const accessStore = useAccessStore();
      const authStore = useAuthStore();
      accessStore.setAccessToken(null);
      if (
        preferences.app.loginExpiredMode === 'modal' &&
        accessStore.isAccessChecked
      ) {
        accessStore.setLoginExpired(true);
      } else {
        await authStore.logout();
      }
    } finally {
      reAuthenticating = false;
    }
  }

  /**
   * 刷新token逻辑
   * 后端刷新接口返回 data = { token, user, expire }
   */
  async function doRefreshToken() {
    const accessStore = useAccessStore();
    const resp = await refreshTokenApi();
    const newToken = resp.data?.token;
    accessStore.setAccessToken(newToken);
    return newToken;
  }

  function formatToken(token: null | string) {
    return token ? `Bearer ${token}` : null;
  }

  // 请求头处理
  client.addRequestInterceptor({
    fulfilled: async (config) => {
      const accessStore = useAccessStore();

      config.headers.Authorization = formatToken(accessStore.accessToken);
      config.headers['x-request-id'] = generateUUID();
      // 后端响应携带过 X-Trace-Id 后，后续请求回带同一 trace-id 串联用户行为
      const traceId = getTraceId();
      if (traceId) {
        config.headers['X-Trace-Id'] = traceId;
      }
      config.headers['Accept-Language'] = preferences.app.locale;
      return config;
    },
  });

  // 提取后端响应头 X-Trace-Id（成功/失败响应均携带），记录后供后续请求回带
  client.addResponseInterceptor({
    fulfilled: async (response) => {
      saveTraceId(response?.headers?.['x-trace-id'] || '');
      return response;
    },
    rejected: async (error) => {
      saveTraceId(error?.response?.headers?.['x-trace-id'] || '');
      throw error;
    },
  });

  // 处理返回的响应数据格式
  client.addResponseInterceptor(
    defaultResponseInterceptor({
      codeField: 'code',
      dataField: 'data',
      successCode: 200,
    }),
  );

  // token过期的处理
  client.addResponseInterceptor(
    authenticateResponseInterceptor({
      client,
      doReAuthenticate,
      doRefreshToken,
      enableRefreshToken: preferences.app.enableRefreshToken,
      formatToken,
    }),
  );

  // 后端统一返回 HTTP 403 + { code: 403, message } 约定：
  // - 登录凭证类消息均含"未登录"或"请重新登录"（未登录，禁止操作！/
  //   登录已失效，请重新登录！/无效的登录凭证，请重新登录！）-> 触发重新认证
  // - 权限不足（"权限不足，禁止操作！"）-> 仅提示错误，不强制登出
  client.addResponseInterceptor({
    rejected: async (error) => {
      const responseData = error?.response?.data ?? {};
      if (error?.response?.status === 403 && responseData?.code === 403) {
        const message = String(responseData?.message ?? '');
        if (message.includes('未登录') || message.includes('请重新登录')) {
          await doReAuthenticate();
        }
      }
      throw error;
    },
  });

  // 通用的错误处理,如果没有进入上面的错误处理逻辑，就会进入这里
  client.addResponseInterceptor(
    errorMessageResponseInterceptor((msg: string, error) => {
      const { ui } = useUi();
      // 这里可以根据业务进行定制,你可以拿到 error 内的信息进行定制化处理，根据不同的 code 做不同的提示，而不是直接使用 message.error 提示 msg
      // 当前mock接口返回的错误字段是 error 或者 message
      const responseData = error?.response?.data ?? {};
      const errorMessage = responseData?.error ?? responseData?.message ?? '';
      // 如果没有错误信息，则会根据状态码进行提示
      ui.notification.error({
        message: errorMessage || msg,
      });
    }),
  );

  return client;
}

export const requestClient = createRequestClient(requestBaseURL, {
  responseReturn: 'data',
});
export const defHttp = createRequestClient(requestBaseURL, {
  responseReturn: 'data',
});
export const baseRequestClient = new RequestClient({ baseURL: requestBaseURL });
