<script lang="ts" setup>
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Card, notification } from 'ant-design-vue';

import { useVbenForm } from '#/adapter/form';

import * as api from './api';
import DeptTree from './dept-tree.vue';

const deptTreeRef = ref();
const selectDeptId = ref<string[]>([]);

const [BaseForm, baseFormApi] = useVbenForm({
  // 所有表单项共用，可单独在表单内覆盖
  commonConfig: {
    // 所有表单项
    componentProps: {
      class: 'w-full',
    },
  },
  // 提交函数
  handleSubmit: onSubmit,
  layout: 'horizontal',
  schema: [
    {
      fieldName: 'dept_id',
      component: 'VbenInput',
      label: 'ID',
      dependencies: {
        show: false,
        triggerFields: ['dept_id'],
      },
    },
    {
      fieldName: 'parent_id',
      component: 'VbenInput',
      label: '上级ID',
      defaultValue: 0,
      dependencies: {
        show: true,
        triggerFields: ['dept_id'],
      },
      componentProps: {
        disabled: true,
        placeholder: '请填写上级ID',
      },
    },
    {
      fieldName: 'dept_name',
      component: 'VbenInput',
      label: '部门名称',
      componentProps: {
        placeholder: '请输入部门名称',
      },
      rules: 'required',
    },
    {
      fieldName: 'sort',
      component: 'InputNumber',
      label: '排序',
      defaultValue: 0,
      componentProps: {
        placeholder: '请填写排序',
        min: 0,
        max: 1000,
      },
      help: '数值越小优先级越高',
    },
    {
      fieldName: 'status',
      component: 'RadioGroup',
      label: '状态',
      defaultValue: 1,
      componentProps: {
        options: [
          { label: '启用', value: 1 },
          { label: '禁用', value: 0 },
        ],
      },
    },
  ],
});

function onSubmit(values: Record<string, any>) {
  api.save(values).then(() => {
    baseFormApi.resetForm();
    deptTreeRef.value?.loadTree();
    notification.success({ duration: 3, message: '保存成功' });
  });
}

function handleSelect(_: any, event: any) {
  if (!event.selected) {
    return;
  }
  const node = event.selectedNodes[0];
  // 树节点为转换后的结构 {id, parentId, label, weight, status}
  baseFormApi.resetForm();
  baseFormApi.setValues({
    dept_id: node.id,
    parent_id: node.parentId,
    dept_name: node.label,
    sort: node.weight,
    status: node.status,
  });
}

function handleAdd(node: any) {
  baseFormApi.resetForm();
  baseFormApi.setValues({
    dept_id: undefined,
    parent_id: node.id,
  });
}
</script>

<template>
  <Page
    content-class="flex flex-row gap-2"
    description="通过部门架构，能够清晰地划分部门职责和个人岗位职责，减少工作重叠和责任不清的情况，提升工作效率。"
    title="部门管理"
  >
    <Card class="w-2/5" title="部门列表">
      <DeptTree
        ref="deptTreeRef"
        v-model:select-dept-id="selectDeptId"
        @add="handleAdd"
        @select="handleSelect"
      />
    </Card>
    <Card class="w-full" title="详情">
      <BaseForm />
    </Card>
  </Page>
</template>
<style lang="less" scoped>
:deep(.p-4) {
  padding: 8px !important;
}
</style>