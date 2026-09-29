<script lang="ts">
import { defineAsyncComponent, defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';

import AclGrantModal from '../../shared/components/AclGrantModal.vue';
import createCrudOptions from './crud';

const ToolsModal = defineAsyncComponent(() => import('./ToolsModal.vue'));

export default defineComponent({
  name: 'McpServerConfigPageList',
  components: {
    AclGrantModal,
    ToolsModal,
  },
  setup() {
    const crudRef = ref();
    const crudBinding = ref();

    // 工具查看弹窗
    const toolsModalVisible = ref(false);
    const selectedMcpServer = ref<any>(null);

    const openToolsModal = (mcpServer: any) => {
      selectedMcpServer.value = mcpServer;
      toolsModalVisible.value = true;
    };

    // 资源授权弹窗（ACL）：整表提交，当前授权行由弹窗自己拉
    const grantModalVisible = ref(false);
    const grantItem = ref<any>(null);

    const openGrantModal = (row: any) => {
      grantItem.value = row;
      grantModalVisible.value = true;
    };

    onMounted(() => {
      const { crudExpose } = useFs({
        crudBinding,
        crudRef,
        createCrudOptions,
        context: {
          openGrantModal,
          openToolsModal,
        },
      });

      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      toolsModalVisible,
      selectedMcpServer,
      grantModalVisible,
      grantItem,
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding" />

    <ToolsModal
      :visible="toolsModalVisible"
      @update:visible="toolsModalVisible = $event"
      :mcp-server="selectedMcpServer"
    />

    <AclGrantModal
      v-if="grantItem"
      v-model:open="grantModalVisible"
      :key="grantItem.id"
      resource-code="mcp"
      :resource-id="grantItem.id"
      :resource-name="grantItem.name"
    />
  </fs-page>
</template>
