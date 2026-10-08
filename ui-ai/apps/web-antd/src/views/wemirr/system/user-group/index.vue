<script lang="ts" setup>
/**
 * 用户组管理页（菜单 component = views/wemirr/system/user-group/index.vue）。
 *
 * 这里只管组本身；把某个知识库授给某个组，是在知识库列表页的授权弹窗里做的事
 * （资源实例授权归 acl_router，与本页权限点 system:usergroup:* 不是一回事）。
 */
import { onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import createCrudOptions from './crud';
import MemberModal from './member-modal.vue';

const memberModalRef = ref<InstanceType<typeof MemberModal> | null>(null);

function memberModal() {
  function openMemberModal(row: { groupId: number; groupName?: string }) {
    memberModalRef.value?.open(row);
  }

  return {
    openMemberModal,
  };
}

const member = memberModal();
const { crudRef, crudBinding, crudExpose } = useFs({
  createCrudOptions,
  context: { member, permission: 'system:usergroup' },
});

// 页面打开后获取列表数据
onMounted(async () => {
  await crudExpose.doRefresh();
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud ref="crudRef" v-bind="crudBinding" />
    <MemberModal ref="memberModalRef" />
  </fs-page>
</template>
