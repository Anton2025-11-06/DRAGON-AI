<script lang="ts">
import { defineAsyncComponent, defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import AclGrantModal from '../../../shared/components/AclGrantModal.vue';
import createCrudOptions from './crud';

const GoalFormDrawer = defineAsyncComponent(() => import('./GoalFormDrawer.vue'));

export default defineComponent({
  name: 'OrchGoalPage',
  components: { AclGrantModal, GoalFormDrawer },
  setup() {
    const crudRef = ref();
    const crudBinding = ref();
    const crudExposeRef = ref<any>(null);

    // 新增/编辑/查看抽屉（readonly 控制查看态）
    const formOpen = ref(false);
    const formGoalId = ref<null | number>(null);
    const formReadonly = ref(false);
    const openFormDrawer = (id: null | number) => {
      formGoalId.value = id ?? null;
      formReadonly.value = false;
      formOpen.value = true;
    };
    const openView = (row: any) => {
      formGoalId.value = row.id;
      formReadonly.value = true;
      formOpen.value = true;
    };

    // 授权弹窗（复用通用 AclGrantModal，resource_code=orch_goal）
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
        context: { openFormDrawer, openGrantModal, openView, removeRow },
      });
      crudExposeRef.value = crudExpose;
      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      formOpen,
      formGoalId,
      formReadonly,
      grantOpen,
      grantItem,
      onSaved: () => crudExposeRef.value?.doRefresh(),
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card orch-list-page">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding" />

    <GoalFormDrawer
      v-model:open="formOpen"
      :goal-id="formGoalId"
      :readonly="formReadonly"
      @saved="onSaved"
    />

    <AclGrantModal
      v-if="grantItem"
      v-model:open="grantOpen"
      :key="grantItem.id"
      resource-code="orch_goal"
      :resource-id="grantItem.id"
      :resource-name="grantItem.name"
    />
  </fs-page>
</template>
