import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
} from '@fast-crud/fast-crud';

import { dict } from '@fast-crud/fast-crud';

import { getExecutionPage, getWorkflowExecutions } from '#/api/ai-workflow';
import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { context } = props;
  const workflowId = context?.workflowId;

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          if (workflowId) {
            return await getWorkflowExecutions(workflowId, query);
          }
          return await getExecutionPage(query);
        },
      },
      table: {
        show: true,
        scroll: { x: 1200 },
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
      rowHandle: {
        width: 80,
        buttons: {
          view: {
            text: '详情',
            type: 'link',
            show: true,
            click: ({ row }: { row: any }) => {
              context?.openDetail?.(row);
            },
          },
          edit: { show: false },
          remove: { show: false },
        },
      },
      columns: {
        id: hiddenIdColumn,
        executionId: {
          title: '执行ID',
          type: 'text',
          search: { show: true },
          column: {
            width: 280,
            ellipsis: true,
            copyable: true,
          },
        },
        workflowName: {
          title: '工作流',
          type: 'text',
          search: { show: !workflowId },
          column: { width: 150 },
        },
        workflowVersion: {
          title: '版本',
          type: 'text',
          column: { width: 80 },
        },
        status: {
          title: '状态',
          type: 'dict-select',
          search: { show: true },
          dict: dict({
            data: [
              { value: 'PENDING', label: '等待中', color: 'default' },
              { value: 'RUNNING', label: '执行中', color: 'processing' },
              { value: 'COMPLETED', label: '已完成', color: 'success' },
              { value: 'FAILED', label: '失败', color: 'error' },
              { value: 'PAUSED', label: '已暂停', color: 'warning' },
              { value: 'CANCELLED', label: '已取消', color: 'default' },
            ],
          }),
          column: { width: 100 },
        },
        duration: {
          title: '耗时',
          type: 'text',
          column: {
            width: 100,
            formatter: ({ value }: { value: number }) => {
              if (!value) return '-';
              if (value < 1000) return `${value}ms`;
              if (value < 60_000) return `${(value / 1000).toFixed(1)}s`;
              return `${(value / 60_000).toFixed(1)}min`;
            },
          },
        },
        startTime: {
          title: '开始时间',
          type: 'text',
          column: {
            width: 180,
            formatter: ({ value }: { value: string }) => {
              if (!value) return '-';
              return new Date(value).toLocaleString('zh-CN');
            },
          },
        },
        endTime: {
          title: '结束时间',
          type: 'text',
          column: {
            width: 180,
            formatter: ({ value }: { value: string }) => {
              if (!value) return '-';
              return new Date(value).toLocaleString('zh-CN');
            },
          },
        },
        errorMessage: {
          title: '错误信息',
          type: 'text',
          column: {
            width: 200,
            ellipsis: true,
          },
        },
        createdTime: {
          title: '创建时间',
          type: 'text',
          column: {
            width: 180,
            formatter: ({ value }: { value: string }) => {
              if (!value) return '-';
              return new Date(value).toLocaleString('zh-CN');
            },
          },
        },
      },
    },
  };
}
