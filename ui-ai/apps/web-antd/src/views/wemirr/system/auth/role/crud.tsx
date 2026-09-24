import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
  UserPageQuery,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { compute, dict } from '@fast-crud/fast-crud';

import {
  booleanDict,
  createTimeReadonlyColumn,
  descriptionColumn,
  hiddenIdColumn,
} from '#/plugin/fast-crud/shared';

import { dataScopeOptions } from './api';
import * as api from './api';

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { assign } = props.context;
  const { hasPermission } = useAccess();
  return {
    crudOptions: {
      request: {
        pageRequest: async (query: UserPageQuery) => await api.GetList(query),
        addRequest: async ({ form }: AddReq) => {
          return await api.AddObj(form);
        },
        editRequest: async ({ form }: EditReq) => {
          const { role_code, ...rest } = form;
          return await api.UpdateObj(rest);
        },
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.role_id),
      },
      table: { size: 'small', scroll: { fixed: true } },
      rowHandle: {
        show: true,
        width: 260,
        align: 'right',
        buttons: {
          view: { show: false },
          edit: {
            show: hasPermission('system:role:edit'),
          },
          remove: {
            show: compute(({ row }) => {
              return hasPermission('system:role:delete') && !row.is_builtin;
            }),
          },
          resource: {
            text: '分配权限',
            type: 'link',
            size: 'small',
            order: 1,
            show: hasPermission('system:role:assign'),
            async click({ row }: any) {
              await assign.resourceModal(row.role_id);
            },
          },
        },
      },
      columns: {
        role_id: hiddenIdColumn,
        role_code: {
          title: '编码',
          type: 'text',
          column: { width: 180 },
          editForm: {
            show: false,
          },
          form: {
            rules: [
              { required: true, message: '请输入编码' },
              { min: 2, max: 64, message: '长度在 2 到 64 个字符' },
            ],
          },
        },
        role_name: {
          title: '名称',
          type: 'text',
          column: { width: 180 },
          search: { show: true },
          form: {
            rules: [
              { required: true, message: '请输入名称' },
              { min: 2, max: 64, message: '长度在 2 到 64 个字符' },
            ],
          },
        },
        data_scope: {
          title: '数据权限',
          type: 'dict-select',
          column: { width: 130, component: { color: 'auto' } },
          addForm: { value: api.DataScopeEnum.SELF },
          dict: dict({ data: dataScopeOptions }),
        },
        is_builtin: {
          title: '内置',
          type: 'dict-radio',
          column: { width: 80, align: 'center' },
          form: { show: false },
          valueBuilder({ value, row, key }: ValueBuilderContext): void {
            // 后端返回 0/1，字典为 boolean 类型，需转换后才能正确显示是/否
            if (value !== null && value !== undefined) {
              row[key] = Boolean(Number(value));
            }
          },
          dict: booleanDict(),
        },
        status: {
          title: '状态',
          type: 'dict-radio',
          search: { show: true },
          column: { width: 100, align: 'center' },
          addForm: { value: 1 },
          dict: dict({
            data: [
              { value: 1, label: '启用', color: 'success' },
              { value: 0, label: '停用', color: 'error' },
            ],
          }),
        },
        description: descriptionColumn,
        create_time: createTimeReadonlyColumn,
      },
    },
  };
}
