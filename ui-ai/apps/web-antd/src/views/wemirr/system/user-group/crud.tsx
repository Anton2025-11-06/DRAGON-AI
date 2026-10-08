import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
  UserPageQuery,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { dict } from '@fast-crud/fast-crud';

import {
  createTimeReadonlyColumn,
  descriptionColumn,
  hiddenIdColumn,
} from '#/plugin/fast-crud/shared';

import * as api from './api';

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { member } = props.context;
  const { hasPermission } = useAccess();
  return {
    crudOptions: {
      request: {
        pageRequest: async (query: UserPageQuery) => await api.GetList(query),
        addRequest: async ({ form }: AddReq) => await api.AddObj(form),
        editRequest: async ({ form }: EditReq) => await api.UpdateObj(form),
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.group_id),
      },
      table: { size: 'small', scroll: { fixed: true } },
      rowHandle: {
        show: true,
        width: 220,
        align: 'right',
        buttons: {
          view: { show: false },
          edit: {
            show: hasPermission('system:usergroup:edit'),
          },
          // 后端删除权限点是 system:usergroup:delete（不是全局约定默认的 :remove），
          // 这里显式对上，否则按钮按 remove 码判定会永远不亮
          remove: {
            show: hasPermission('system:usergroup:delete'),
          },
          member: {
            text: '成员',
            type: 'link',
            size: 'small',
            order: 1,
            show: hasPermission('system:usergroup:member'),
            async click({ row }: any) {
              await member.openMemberModal({
                groupId: row.group_id,
                groupName: row.group_name,
              });
            },
          },
        },
      },
      columns: {
        group_id: hiddenIdColumn,
        group_name: {
          title: '组名',
          type: 'text',
          column: { width: 180 },
          search: { show: true },
          form: {
            rules: [
              { required: true, message: '请输入组名' },
              { min: 1, max: 64, message: '长度在 1 到 64 个字符' },
            ],
          },
        },
        member_count: {
          title: '成员数',
          type: 'text',
          column: { width: 90, align: 'center' },
          // 只读统计列：由后端 group by 算出，表单里没有这一项
          form: { show: false },
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
