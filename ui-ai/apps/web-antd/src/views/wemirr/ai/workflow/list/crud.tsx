import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
} from '@fast-crud/fast-crud';

import { dict } from '@fast-crud/fast-crud';

import { deleteWorkflow, getWorkflowPage } from '#/api/ai-workflow';
import { createTimeColumn, hiddenIdColumn } from '#/plugin/fast-crud/shared';

export default function crud(
  _props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          return await getWorkflowPage(query);
        },
        delRequest: async ({ row }: any) => {
          return await deleteWorkflow(row.id);
        },
      },
      table: {
        show: false, // 使用卡片布局
        scrollToFirstRowOnChange: false,
      },
      pagination: {
        show: true,
      },
      actionbar: {
        buttons: {
          add: { show: false },
          export: { show: false },
          import: { show: false },
        },
      },
      form: {
        display: 'flex',
        wrapper: {
          width: '600px',
        },
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '名称',
          type: 'text',
          search: { show: true },
          form: {
            rules: [{ required: true, message: '请输入工作流名称' }],
            component: {
              placeholder: '请输入工作流名称',
            },
          },
          column: { width: 200 },
        },
        description: {
          title: '描述',
          type: 'textarea',
          search: { show: false },
          form: {
            col: { span: 24 },
            component: {
              placeholder: '请输入工作流描述',
              rows: 3,
              maxlength: 500,
              showCount: true,
            },
          },
          column: {
            width: 300,
            ellipsis: true,
          },
        },
        status: {
          title: '状态',
          type: 'dict-select',
          search: { show: true },
          form: { show: false },
          dict: dict({
            data: [
              { value: 'DRAFT', label: '草稿', color: 'default' },
              { value: 'PUBLISHED', label: '已发布', color: 'success' },
            ],
          }),
          column: { width: 100 },
        },
        currentVersion: {
          title: '版本',
          type: 'text',
          form: { show: false },
          column: { width: 80 },
        },
        nodeCount: {
          title: '节点数',
          type: 'text',
          form: { show: false },
          column: { width: 80 },
        },
        createTime: createTimeColumn,
        updateTime: {
          title: '更新时间',
          type: 'datetime',
          form: { show: false },
          column: { width: 180 },
        },
      },
    },
  };
}
