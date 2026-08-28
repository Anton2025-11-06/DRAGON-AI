<script lang="ts" setup name="OptLogPage">
import { onMounted } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import createCrudOptions from './opt-log';

const { crudBinding, crudRef, crudExpose } = useFs({ createCrudOptions });
// 页面打开后获取列表数据
onMounted(async () => {
  await crudExpose.doRefresh();
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud ref="crudRef" v-bind="crudBinding">
      <template #cell_operation="scope">
        <a-tooltip :title="scope.row.operation" placement="top">
          {{ scope.row.operation }}
        </a-tooltip>
      </template>
      <template #cell_path="scope">
        <a-tooltip :title="scope.row.path" placement="top">
          {{ scope.row.path }}
        </a-tooltip>
      </template>
    </fs-crud>
  </fs-page>
</template>