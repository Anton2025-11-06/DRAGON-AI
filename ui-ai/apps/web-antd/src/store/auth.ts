import type { Recordable, UserInfo } from '@vben/types';

import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';
import { resetAllStores, useAccessStore, useUserStore } from '@vben/stores';

import { notification } from 'ant-design-vue';
import { defineStore } from 'pinia';

import { getUserInfoApi, loginApi, logoutApi } from '#/api';
import { $t } from '#/locales';

/**
 * 后端用户信息 -> vben UserInfo 映射
 * 后端 me/info 返回 { user, roles, role_names, permissions, menus }
 */
function mapUserInfo(data: Record<string, any>): UserInfo {
  const user = data?.user ?? data ?? {};
  const roles = user.roles ?? data?.roles ?? [];
  return {
    userId: String(user.user_id ?? ''),
    username: user.username ?? '',
    nickname: user.real_name || user.username || '',
    avatar: user.avatar ?? '',
    roles,
    desc: user.description ?? '',
    // 个人中心展示：后端 me/info 返回 user.phone / user.email
    email: user.email ?? '',
    mobile: user.phone ?? '',
    homePath: preferences.app.defaultHomePath,
    token: '',
    realName: user.real_name ?? '',
    deptName: user.dept_name ?? '',
    // 权限码（me/info 返回 permissions），登录时随用户信息一次取全，避免重复请求接口
    permissions: (data?.permissions ??
      data?.user?.permissions ??
      []) as string[],
  };
}

export const useAuthStore = defineStore('auth', () => {
  const accessStore = useAccessStore();
  const userStore = useUserStore();
  const router = useRouter();

  const loginLoading = ref(false);

  /**
   * 异步处理登录操作
   * Asynchronously handle the login process
   * @param params 登录表单数据
   * @param onSuccess onSuccess
   */
  async function authLogin(
    params: Recordable<any>,
    onSuccess?: () => Promise<void> | void,
  ) {
    // 异步处理用户登录操作并获取 Token
    let userInfo: null | UserInfo = null;
    try {
      loginLoading.value = true;
      // 后端登录返回 data = { token, user, expire }
      const loginResult = await loginApi(params);
      const accessToken = loginResult?.token;

      // 如果成功获取到 Token
      if (accessToken) {
        accessStore.setAccessToken(accessToken);

        // 用户信息与权限码同源（/api/system/users/me/info 一次返回），单请求取全
        userInfo = await fetchUserInfo();
        const accessCodes = userInfo.permissions ?? [];

        userStore.setUserInfo(userInfo);
        accessStore.setAccessCodes(accessCodes);

        if (accessStore.loginExpired) {
          accessStore.setLoginExpired(false);
        } else {
          onSuccess
            ? await onSuccess?.()
            : await router.push(
                userInfo.homePath || preferences.app.defaultHomePath,
              );
        }

        if (userInfo?.nickname) {
          notification.success({
            description: `${$t('authentication.loginSuccessDesc')}:${userInfo?.nickname}`,
            duration: 3,
            message: $t('authentication.loginSuccess'),
          });
        }
      }
    } finally {
      loginLoading.value = false;
    }

    return {
      userInfo,
    };
  }

  async function logout(redirect: boolean = true) {
    try {
      await logoutApi();
    } catch {
      // 不做任何处理
    }
    resetAllStores();
    accessStore.setLoginExpired(false);
    // 回登录页带上当前路由地址
    await router.replace({
      path: LOGIN_PATH,
      query: redirect
        ? {
            redirect: encodeURIComponent(router.currentRoute.value.fullPath),
          }
        : {},
    });
  }

  async function fetchUserInfo() {
    let userInfo: null | UserInfo = null;
    // 后端 GET /api/system/users/me/info 返回 { user, roles, role_names, permissions, menus }
    const data = await getUserInfoApi();
    userInfo = mapUserInfo(data);
    userStore.setUserInfo(userInfo);
    return userInfo;
  }

  function $reset() {
    loginLoading.value = false;
  }

  return {
    $reset,
    authLogin,
    fetchUserInfo,
    loginLoading,
    logout,
  };
});
