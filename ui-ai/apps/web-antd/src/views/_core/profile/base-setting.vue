<script setup lang="ts">
import type { VbenFormSchema } from '#/adapter/form';

import { computed, onMounted, ref } from 'vue';

import { ProfileBaseSetting, z } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';

const userStore = useUserStore();
const profileBaseSettingRef = ref();

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      fieldName: 'username',
      component: 'Input',
      label: '用户名',
      componentProps: {
        disabled: true,
        placeholder: '用户名不可修改',
      },
    },
    {
      fieldName: 'deptName',
      component: 'Input',
      label: '所属部门',
      componentProps: {
        disabled: true,
        placeholder: '暂无部门',
      },
    },
    {
      fieldName: 'nickname',
      component: 'Input',
      label: '真实姓名',
      componentProps: {
        maxlength: 50,
        placeholder: '请输入真实姓名',
        showCount: true,
      },
      rules: z.string().max(50, '真实姓名不能超过50个字符'),
    },
    {
      fieldName: 'email',
      component: 'Input',
      label: '邮箱',
      componentProps: {
        maxlength: 50,
        placeholder: '请输入邮箱',
        showCount: true,
      },
      rules: z
        .string()
        .max(50, '邮箱不能超过50个字符')
        .email('邮箱格式不正确'),
    },
    {
      fieldName: 'mobile',
      component: 'Input',
      label: '手机号',
      componentProps: {
        placeholder: '请输入手机号',
      },
      rules: z.string().regex(/^1\d{10}$/, '手机号格式不正确'),
    },
  ];
});

/** 初始化表单数据 */
onMounted(() => {
  const info = userStore.userInfo;
  if (info) {
    profileBaseSettingRef.value?.getFormApi()?.setValues({
      username: info.username,
      deptName: info.deptName ?? '',
      nickname: info.nickname,
      email: info.email ?? '',
      mobile: info.mobile ?? '',
    });
  }
});

/** 提交更新 */
async function handleSubmit(_values: Record<string, any>) {
  // 后端暂未提供个人资料修改接口，仅提示保留交互入口
  message.warning('后端暂未开放个人资料修改接口');
}
</script>

<template>
  <ProfileBaseSetting
    ref="profileBaseSettingRef"
    class="w-full min-w-[420px] max-w-[820px]"
    :form-schema="formSchema"
    @submit="handleSubmit"
  />
</template>

<style scoped>
.base-setting :deep(.vben-form) {
  --vben-form-label-width: 80px;
}

.base-setting :deep(.ant-input),
.base-setting :deep(.ant-input-affix-wrapper),
.base-setting :deep(.ant-select-selector),
.base-setting :deep(textarea.ant-input) {
  border-radius: 6px;
}

.base-setting :deep(.vben-button) {
  min-width: 120px;
  border-radius: 6px;
}
</style>
