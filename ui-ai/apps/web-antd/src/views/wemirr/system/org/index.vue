<script lang="ts" setup>
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { ApartmentOutlined, ProfileOutlined } from '@ant-design/icons-vue';
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
    auto-content-height
    content-class="dept-page-content"
    description="维护组织层级、部门排序与启用状态。"
    title="部门管理"
  >
    <Card class="dept-tree-card" :bordered="false">
      <template #title>
        <span class="dept-card-title">
          <ApartmentOutlined />
          组织架构
        </span>
      </template>
      <template #extra>
        <span class="card-caption">选择节点查看详情</span>
      </template>
      <DeptTree
        ref="deptTreeRef"
        v-model:select-dept-id="selectDeptId"
        @add="handleAdd"
        @select="handleSelect"
      />
    </Card>

    <Card class="dept-detail-card" :bordered="false">
      <template #title>
        <span class="dept-card-title">
          <ProfileOutlined />
          部门详情
        </span>
      </template>
      <div class="detail-intro">
        <h3>部门信息</h3>
        <p>从左侧选择部门进行编辑，或点击节点旁的新增按钮创建下级部门。</p>
      </div>
      <div class="dept-form-body">
        <BaseForm />
      </div>
    </Card>
  </Page>
</template>

<style lang="less" scoped>
:deep(.dept-page-content) {
  /* Page 的内联自动高度依赖该变量；未初始化时用真实视口高度兜底，
     再减去 Page 自己测得的标题栏高度，保证底边与主内容区一致。 */
  --vben-content-height: calc(
    100vh - var(--vben-header-height, 90px)
  );

  display: flex;
  gap: 16px;
  align-items: stretch;
  padding: 16px;
}

.dept-tree-card {
  flex: 0 0 340px;
  min-width: 0;
}

.dept-detail-card {
  flex: 1;
  min-width: 0;
}

.dept-tree-card,
.dept-detail-card {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--border-color, #e5eaf0);
  border-radius: 10px;
  box-shadow: 0 3px 12px rgb(15 23 42 / 4%);

  :deep(.ant-card-head) {
    flex: none;
    min-height: 52px;
    padding: 0 18px;
    background: var(--component-background-light, #fafbfc);
    border-bottom-color: var(--border-color, #edf0f2);
  }

  :deep(.ant-card-body) {
    flex: 1;
    min-height: 0;
    padding: 18px;
  }
}

.dept-tree-card :deep(.ant-card-body) {
  overflow: hidden;
}

.dept-detail-card {
  :deep(.ant-card-body) {
    padding: 22px 28px;
    overflow-y: auto;
  }
}

.dept-card-title {
  display: inline-flex;
  gap: 9px;
  align-items: center;
  font-size: 14px;
  font-weight: 600;

  :deep(.anticon) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 9%);
    border-radius: 7px;
  }
}

.card-caption {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-color-secondary, #8a9199);
}

.detail-intro {
  width: 100%;
  max-width: 760px;
  padding: 14px 16px;
  margin: 0 auto 24px;
  background: hsl(var(--primary) / 4%);
  border: 1px solid hsl(var(--primary) / 9%);
  border-left: 3px solid hsl(var(--primary));
  border-radius: 8px;

  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    line-height: 22px;
  }

  p {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-color-secondary, #747d89);
  }
}

.dept-form-body {
  width: 100%;
  max-width: 760px;
  margin: 0 auto;

  :deep(.ant-form-item) {
    margin-bottom: 22px;
  }

  :deep(.ant-form-item-label > label) {
    font-weight: 500;
  }

  :deep(.ant-input),
  :deep(.ant-input-number),
  :deep(.ant-input-affix-wrapper) {
    border-radius: 6px;
  }

  :deep(.ant-form-item-control-input-content) {
    > .ant-btn {
      min-width: 76px;
      margin-left: 10px;
    }
  }
}

@media (max-width: 900px) {
  :deep(.dept-page-content) {
    flex-direction: column;
    padding: 8px;
  }

  .dept-tree-card {
    flex-basis: auto;
  }

  .dept-detail-card {
    :deep(.ant-card-body) {
      padding: 16px;
    }
  }
}
</style>
