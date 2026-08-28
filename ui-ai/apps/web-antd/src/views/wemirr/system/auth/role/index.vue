<script lang="ts" setup>
import { onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import AssignResource from './assign-resource.vue';
import createCrudOptions from './crud';

const assignResourceRef = ref<InstanceType<typeof AssignResource> | null>(null);

function assignModal() {
  function resourceModal(roleId: number) {
    assignResourceRef.value?.open({ roleId });
  }

  return {
    resourceModal,
  };
}

const assign = assignModal();
const { crudRef, crudBinding, crudExpose } = useFs({
  createCrudOptions,
  context: { assign, permission: 'system:role' },
});

// 页面打开后获取列表数据
onMounted(async () => {
  await crudExpose.doRefresh();
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud ref="crudRef" v-bind="crudBinding">
      <template #cell_description="scope">
        <a-tooltip :title="scope.row.description" placement="top">
          {{ scope.row.description }}
        </a-tooltip>
      </template>
    </fs-crud>
    <AssignResource ref="assignResourceRef" />
  </fs-page>
</template>