<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed, ref } from 'vue';

import { AuthenticationLogin, z } from '@vben/common-ui';
import { $t } from '@vben/locales';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();
const loginRef = ref();

const formSchema = computed((): VbenFormSchema[] => {
  // 不预填任何账号密码：登录页是公网可访问的入口，写死默认凭据等于把管理员账号公开挂出去
  return [
    {
      component: 'VbenInput',
      componentProps: {
        placeholder: $t('authentication.usernameTip'),
      },
      fieldName: 'username',
      label: $t('authentication.username'),
      rules: z.string().min(1, { message: $t('authentication.usernameTip') }),
    },
    {
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: $t('authentication.password'),
      },
      fieldName: 'password',
      label: $t('authentication.password'),
      rules: z.string().min(1, { message: $t('authentication.passwordTip') }),
    },
  ];
});

/**
 * 点击登录按钮 - 直接调用登录接口
 */
async function handleSubmit(
  params: any,
  onSuccess?: () => Promise<void> | void,
) {
  await authStore.authLogin(
    {
      username: params.username,
      password: params.password,
    },
    onSuccess,
  );
}
</script>

<template>
  <AuthenticationLogin
    ref="loginRef"
    :form-schema="formSchema"
    :loading="authStore.loginLoading"
    :show-code-login="false"
    :show-forget-password="false"
    :show-qrcode-login="false"
    :show-register="true"
    :show-remember-me="true"
    :show-third-party-login="false"
    :sub-title="$t('authentication.loginSubtitle')"
    :title="$t('authentication.welcomeBack')"
    @submit="handleSubmit"
  />
</template>
