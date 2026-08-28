<script lang="ts" setup name="UserPageList">
import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { useFs } from '@fast-crud/fast-crud';
import { Card, message, Modal } from 'ant-design-vue';

import { defHttp } from '#/api/request';

import * as api from './api';
import createCrudOptions from './crud';

const { crudBinding, crudRef, crudExpose } = useFs({
  createCrudOptions,
  context: { permission: 'system:user', resetPwd, assignRole },
});

onMounted(async () => {
  await crudExpose?.doRefresh();
});

// ==================== 重置密码 ====================
const resetPwdOpen = ref(false);
const resetPwdForm = reactive({ user_id: 0, username: '', password: '' });
const resetPwdSaving = ref(false);

function resetPwd(row: any) {
  resetPwdForm.user_id = row.user_id;
  resetPwdForm.username = row.username;
  resetPwdForm.password = '';
  resetPwdOpen.value = true;
}

async function handleResetPwd() {
  if (!resetPwdForm.password || resetPwdForm.password.length < 6) {
    message.warning('请输入至少 6 位的新密码');
    return;
  }
  resetPwdSaving.value = true;
  try {
    await api.ResetPassword(String(resetPwdForm.user_id), resetPwdForm.password);
    message.success('密码重置成功');
    resetPwdOpen.value = false;
  } catch {
    // 错误信息由全局拦截器提示
  } finally {
    resetPwdSaving.value = false;
  }
}

// ==================== 分配角色 ====================
const assignOpen = ref(false);
const roleOptions = ref<any[]>([]);
const assignForm = reactive({ user_id: 0, username: '', role_ids: [] as number[] });
const assignSaving = ref(false);

async function loadRoleOptions() {
  // GET /api/system/roles/all 全部启用角色
  roleOptions.value = (await defHttp.get('/api/system/roles/all')) ?? [];
}

async function assignRole(row: any) {
  assignOpen.value = true;
  assignForm.user_id = row.user_id;
  assignForm.username = row.username;
  assignForm.role_ids = [];
  // 回显当前角色
  const roles = (await api.GetUserRoles(String(row.user_id))) ?? [];
  assignForm.role_ids = roles.map((r: any) => r.role_id);
}

async function handleAssignRole() {
  assignSaving.value = true;
  try {
    await api.AssignRoles(String(assignForm.user_id), assignForm.role_ids);
    message.success('角色分配成功');
    assignOpen.value = false;
    await crudExpose?.doRefresh();
  } catch {
    // 错误信息由全局拦截器提示
  } finally {
    assignSaving.value = false;
  }
}

loadRoleOptions();
</script>

<template>
  <Page content-class="flex flex-row gap-2" :auto-content-height="true">
    <Card class="sys-user-page-card w-full" title="用户管理">
      <fs-crud ref="crudRef" v-bind="crudBinding" />
    </Card>

    <!-- 重置密码弹窗 -->
    <Modal
      v-model:open="resetPwdOpen"
      title="重置密码"
      :confirm-loading="resetPwdSaving"
      ok-text="确认重置"
      cancel-text="取消"
      @ok="handleResetPwd"
    >
      <p class="mb-2">
        将重置用户 <b>{{ resetPwdForm.username }}</b> 的登录密码，请设置新密码：
      </p>
      <a-input-password
        v-model:value="resetPwdForm.password"
        placeholder="请输入新密码（至少 6 位）"
        allow-clear
      />
    </Modal>

    <!-- 分配角色弹窗 -->
    <Modal
      v-model:open="assignOpen"
      title="分配角色"
      :confirm-loading="assignSaving"
      ok-text="确认分配"
      cancel-text="取消"
      @ok="handleAssignRole"
    >
      <p class="mb-2">
        为用户 <b>{{ assignForm.username }}</b> 分配角色：
      </p>
      <a-select
        v-model:value="assignForm.role_ids"
        mode="multiple"
        placeholder="请选择角色"
        :options="roleOptions.map((r) => ({ label: r.role_name, value: r.role_id }))"
        option-filter-prop="label"
        allow-clear
        class="w-full"
      />
    </Modal>
  </Page>
</template>

<style lang="less" scoped>
:deep(.p-4) {
  padding: 8px !important;
}

:deep(.sys-user-page-card) {
  .fs-crud-container {
    height: calc(100vh - 250px);
  }

  .ant-card-body {
    padding: 8px;
  }
}
</style>