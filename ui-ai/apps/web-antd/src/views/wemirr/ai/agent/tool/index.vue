<script lang="ts">
import { defineAsyncComponent, defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

import * as api from './api';
import createCrudOptions from './crud';

const TestModal = defineAsyncComponent(() => import('./TestModal.vue'));
const ToolFormModal = defineAsyncComponent(() => import('./ToolFormModal.vue'));

export default defineComponent({
  name: 'DynamicToolPageList',
  components: {
    TestModal,
    ToolFormModal,
  },
  setup() {
    const crudRef = ref();
    const crudBinding = ref();
    const crudExposeRef = ref<any>(null);

    // 运行测试弹窗
    const testModalVisible = ref(false);
    const selectedTool = ref<any>(null);

    const openTestModal = (tool: any) => {
      selectedTool.value = tool;
      testModalVisible.value = true;
    };

    // 新增 / 编辑弹窗（自定义表单，排版对齐工作流代码节点）
    const formModalVisible = ref(false);
    const editingToolId = ref<null | number>(null);

    const openFormModal = (id: null | number) => {
      editingToolId.value = id ?? null;
      formModalVisible.value = true;
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
          openFormModal,
          openTestModal,
          toggleStatus,
        },
      });
      crudExposeRef.value = crudExpose;
      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      testModalVisible,
      selectedTool,
      formModalVisible,
      editingToolId,
      // 自定义弹窗保存后刷新列表（新增行不在当前列表里，不刷看不出区别）
      onFormSaved: () => crudExposeRef.value?.doRefresh(),
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

    <ToolFormModal
      :open="formModalVisible"
      :tool-id="editingToolId"
      @update:open="formModalVisible = $event"
      @saved="onFormSaved"
    />
  </fs-page>
</template>
