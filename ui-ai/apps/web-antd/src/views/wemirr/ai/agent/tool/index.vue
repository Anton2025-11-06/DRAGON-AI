<script lang="ts">
import { defineAsyncComponent, defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

import * as api from './api';
import createCrudOptions from './crud';

const TestModal = defineAsyncComponent(() => import('./TestModal.vue'));

export default defineComponent({
  name: 'DynamicToolPageList',
  components: {
    TestModal,
  },
  setup() {
    const crudRef = ref();
    const crudBinding = ref();

    // 运行测试弹窗
    const testModalVisible = ref(false);
    const selectedTool = ref<any>(null);

    const openTestModal = (tool: any) => {
      selectedTool.value = tool;
      testModalVisible.value = true;
    };

    const toggleStatus = async (row: any, status: boolean) => {
      try {
        await api.ToggleStatus(row.id, status);
        message.success(status ? '已启用' : '已停用');
      } catch (error: any) {
        message.error(`状态更新失败: ${error.message}`);
        row.status = !status;
      }
    };

    onMounted(() => {
      const { crudExpose } = useFs({
        crudBinding,
        crudRef,
        createCrudOptions,
        context: {
          openTestModal,
          toggleStatus,
        },
      });
      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      testModalVisible,
      selectedTool,
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding" />

    <TestModal
      :visible="testModalVisible"
      @update:visible="testModalVisible = $event"
      :tool="selectedTool"
    />
  </fs-page>
</template>
