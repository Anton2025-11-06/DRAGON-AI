<script lang="ts">
import { defineAsyncComponent, defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import AclGrantModal from '../../../shared/components/AclGrantModal.vue';
import createCrudOptions from './crud';

const TeamFormDrawer = defineAsyncComponent(() => import('./TeamFormDrawer.vue'));
const ValidateConsole = defineAsyncComponent(() => import('./ValidateConsole.vue'));

export default defineComponent({
  name: 'OrchTeamPage',
  components: { AclGrantModal, TeamFormDrawer, ValidateConsole },
  setup() {
    const crudRef = ref();
    const crudBinding = ref();
    const crudExposeRef = ref<any>(null);

    // 新增/编辑/查看抽屉（readonly 控制查看态）
    const formOpen = ref(false);
    const formTeamId = ref<null | number>(null);
    const formReadonly = ref(false);
    const openFormDrawer = (id: null | number) => {
      formTeamId.value = id ?? null;
      formReadonly.value = false;
      formOpen.value = true;
    };
    const openView = (row: any) => {
      formTeamId.value = row.id;
      formReadonly.value = true;
      formOpen.value = true;
    };

    // 验证台
    const validateOpen = ref(false);
    const validateTeamId = ref<null | number>(null);
    const openValidate = (row: any) => {
      validateTeamId.value = row.id;
      validateOpen.value = true;
    };

    // 授权弹窗（复用通用 AclGrantModal，resource_code=orch_team）
    const grantOpen = ref(false);
    const grantItem = ref<any>(null);
    const openGrantModal = (row: any) => {
      grantItem.value = row;
      grantOpen.value = true;
    };

    const removeRow = (row: any) => {
      crudExposeRef.value?.doRemove({ row }, { noConfirm: true });
    };

    onMounted(() => {
      const { crudExpose } = useFs({
        crudBinding,
        crudRef,
        createCrudOptions,
        context: { openFormDrawer, openGrantModal, openValidate, openView, removeRow },
      });
      crudExposeRef.value = crudExpose;
      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      formOpen,
      formTeamId,
      formReadonly,
      validateOpen,
      validateTeamId,
      grantOpen,
      grantItem,
      onSaved: () => crudExposeRef.value?.doRefresh(),
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding" />

    <TeamFormDrawer
      v-model:open="formOpen"
      :team-id="formTeamId"
      :readonly="formReadonly"
      @saved="onSaved"
    />

    <ValidateConsole v-model:open="validateOpen" :team-id="validateTeamId" />

    <AclGrantModal
      v-if="grantItem"
      v-model:open="grantOpen"
      :key="grantItem.id"
      resource-code="orch_team"
      :resource-id="grantItem.id"
      :resource-name="grantItem.name"
    />
  </fs-page>
</template>
