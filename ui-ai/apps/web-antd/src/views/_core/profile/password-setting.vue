<script setup lang="ts">
import type { VbenFormSchema } from '#/adapter/form';

import { computed, ref } from 'vue';

import { ProfilePasswordSetting, z } from '@vben/common-ui';

import { message } from 'ant-design-vue';

import { updateUserPasswordApi } from '#/api';
import { useAuthStore } from '#/store/auth';
import { useUserStore } from '@vben/stores';

const profilePasswordSettingRef = ref();
const authStore = useAuthStore();
const userStore = useUserStore();
const passwordPattern =
  /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$/;

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      fieldName: 'currentPassword',
      label: '当前密码',
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: '请输入当前密码',
      },
      rules: z.string().min(1, '请输入当前密码'),
    },
    {
      fieldName: 'newPassword',
      label: '新密码',
      component: 'VbenInputPassword',
      componentProps: {
        passwordStrength: true,
        placeholder: '至少8位，包含大小写字母、数字和特殊字符',
      },
      rules: z.string().regex(passwordPattern, '密码不符合复杂性要求'),
    },
    {
      fieldName: 'confirmPassword',
      label: '确认密码',
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: '请再次输入新密码',
      },
      rules: z.string().min(1, '请再次输入新密码'),
      dependencies: {
        rules(values) {
          const { newPassword } = values;
          return z
            .string()
            .min(1, '请再次输入新密码')
            .refine((value) => value === newPassword, {
              message: '两次输入的密码不一致',
            });
        },
        triggerFields: ['newPassword'],
      },
    },
  ];
});

/** 提交修改密码 */
async function handleSubmit(values: Record<string, any>) {
  try {
    // 对接后端 PUT /api/system/users/{user_id}/own-password
    await updateUserPasswordApi(Number(userStore.userInfo?.userId), {
      old_password: values.currentPassword,
      new_password: values.newPassword,
    });
    message.success('密码修改成功，请重新登录');
    profilePasswordSettingRef.value?.getFormApi()?.resetForm();
    await authStore.logout(false);
  } catch {
    message.error('密码修改失败');
  }
}
</script>

<template>
  <ProfilePasswordSetting
    ref="profilePasswordSettingRef"
    class="w-full min-w-[420px] max-w-[820px]"
    :form-schema="formSchema"
    @submit="handleSubmit"
  />
</template>
