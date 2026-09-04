<script lang="ts">
import { defineComponent, onMounted, ref } from 'vue';

import { useFs } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

import * as api from './api';
import createCrudOptions from './apply-crud';

export default defineComponent({
  name: 'ModelApplyAuditPage',
  setup() {
    const crudRef = ref();
    const crudBinding = ref();

    // 拒绝原因弹窗
    const rejectVisible = ref(false);
    const rejectRow = ref<any>(null);
    const rejectReason = ref('');

    const ApplyModal = (row: any) => {
      rejectRow.value = row;
      rejectReason.value = '';
      rejectVisible.value = true;
    };

    const submitReject = async () => {
      await api.AuditApply(rejectRow.value.id, false, rejectReason.value);
      message.success('已拒绝');
      rejectVisible.value = false;
      crudExpose?.doRefresh();
    };

    // 可变上下文：crudExpose 由 useFs 返回后回填（供行按钮点击时刷新列表）
    const ctx: any = {
      ApplyModal,
    };
    let crudExpose: any = null;
    onMounted(() => {
      const result = useFs({
        crudBinding,
        crudRef,
        createCrudOptions,
        context: ctx,
      });
      crudExpose = result.crudExpose;
      ctx.crudExpose = crudExpose;
      crudExpose.doRefresh();
    });

    return {
      crudBinding,
      crudRef,
      rejectVisible,
      rejectRow,
      rejectReason,
      submitReject,
    };
  },
});
</script>

<template>
  <fs-page class="page-layout-card">
    <fs-crud v-if="crudBinding" ref="crudRef" v-bind="crudBinding" />

    <!-- 拒绝审批 -->
    <a-modal
      v-model:open="rejectVisible"
      title="拒绝申请"
      width="460px"
      @ok="submitReject"
    >
      <a-form layout="vertical">
        <a-form-item label="申请人">
          <a-input :value="rejectRow?.username" disabled />
        </a-form-item>
        <a-form-item label="模型">
          <a-input :value="rejectRow?.model_name" disabled />
        </a-form-item>
        <a-form-item label="拒绝原因" required>
          <a-textarea
            v-model:value="rejectReason"
            :rows="3"
            placeholder="请填写拒绝原因，申请人将可见"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </fs-page>
</template>
