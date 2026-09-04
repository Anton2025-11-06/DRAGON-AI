<script lang="ts" setup name="OnlinePage">
import { onMounted } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import createCrudOptions from './online';

const { crudBinding, crudRef, crudExpose } = useFs({ createCrudOptions });
// 页面打开后获取在线用户列表
onMounted(async () => {
  await crudExpose.doRefresh();
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud ref="crudRef" v-bind="crudBinding" />
  </fs-page>
</template>