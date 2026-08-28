import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
} from '@fast-crud/fast-crud';

import { computed } from 'vue';

import { dict } from '@fast-crud/fast-crud';

import * as api from './api';

export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { onPreview, onReplace, toggleStatus } = props.context;
  return {
    crudOptions: {
      request: {
        transformQuery: ({ page, form, sort }: any) => {
          const order = sort === null ? {} : { column: sort.prop, asc: sort.asc };
          return {
            current: page.currentPage ?? 1,
            size: page.pageSize ?? 12,
            ...form,
            ...order,
          };
        },
        pageRequest: async (query: any) => await api.PageList(query),
        delRequest: async ({ row }: any) => await api.DelObj(row.id),
      },
      toolbar: {},
      actionbar: {
        show: true,
        buttons: {
          add: {
            show: false,
          },
        },
      },
      table: {
        class: computed(() => 'hidden'),
      },
      rowHandle: {
        width: 180,
        buttons: {
          add: { show: false },
          view: { show: false },
          edit: {
            type: 'link',
            text: '替换目录',
            size: 'small',
            title: '替换技能文件夹',
            order: 1,
            click(context: any) {
              onReplace?.(context.row);
            },
          },
          preview: {
            type: 'link',
            text: '预览',
            size: 'small',
            title: '技能预览',
            order: 0,
            click(context: any) {
              onPreview?.(context.row);
            },
          },
          remove: { order: 2 },
        },
      },
      columns: {
        id: {
          title: 'ID',
          type: 'text',
          form: { show: false },
          column: { show: false },
        },
        name: {
          title: '技能名称',
          type: 'text',
          search: { show: true },
          column: { ellipsis: true, width: 180 },
        },
        category: {
          title: '分类',
          type: 'text',
          search: { show: true },
          column: { ellipsis: true, width: 120 },
        },
        status: {
          title: '启用状态',
          type: 'dict-switch',
          search: { show: true },
          column: { width: 100 },
          dict: dict({
            data: [
              { label: '启用', value: true },
              { label: '禁用', value: false },
            ],
          }),
          valueChange({ row, value }: any) {
            if (row?.id !== undefined) {
              toggleStatus?.(row, Boolean(value));
            }
          },
        },
        skillPath: {
          title: '技能目录',
          type: 'text',
          column: { ellipsis: true, width: 240 },
          form: { show: false },
        },
        skillFile: {
          title: '入口文件',
          type: 'text',
          column: { ellipsis: true, width: 240 },
          form: { show: false },
        },
        resourceCount: {
          title: '资源数',
          type: 'number',
          column: { width: 90 },
          form: { show: false },
        },
      },
    },
  };
}
