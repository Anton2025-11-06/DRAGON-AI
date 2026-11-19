import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { dict } from '@fast-crud/fast-crud';

import { aclRowButton } from '#/plugin/fast-crud/acl-row';
import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';

/**
 * 目标列表配置（与单/团队两层权限口径一致，资源编码 orch_goal）：
 * - 新建入口 show = hasPermission('agent:orch:goal:add')（RBAC 全局入口）；
 * - 行级按钮（查看/使用/编辑/删除/验证）纯由 ACL actions 驱动；
 *   授权两层 AND：show = hasPermission('agent:orch:goal:grant') + action 'share'。
 */
export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { openFormDrawer, openGrantModal, openValidate, openView, removeRow } =
    props.context || {};
  const { hasPermission } = useAccess();

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) =>
          await api.PageList({
            keyword: query.keyword,
            page: query.page ?? 1,
            page_size: query.pageSize ?? 10,
            status: query.status,
          }),
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.id),
      },
      actionbar: {
        buttons: {
          add: {
            show: hasPermission('agent:orch:goal:add'),
            click() {
              openFormDrawer?.(null);
            },
          },
        },
      },
      form: {
        colSpan: 6,
      },
      columns: {
        id: hiddenIdColumn,
        keyword: {
          title: '名称/用途',
          type: 'text',
          search: { show: true, title: '名称/用途' },
          column: { show: false },
          form: { component: { placeholder: '按名称或用途检索' } },
        },
        name: {
          title: '目标名称',
          type: 'text',
          column: { width: 160, ellipsis: true },
        },
        purpose: {
          title: '用途',
          type: 'text',
          column: { width: 200, ellipsis: true },
        },
        executorName: {
          title: '执行者',
          type: 'text',
          column: { width: 120, ellipsis: true },
        },
        verifierName: {
          title: '检查者',
          type: 'text',
          column: { width: 120, ellipsis: true },
        },
        status: {
          title: '状态',
          type: 'dict-select',
          search: { show: true },
          dict: statusDict(),
          column: { width: 90 },
        },
        create_time: {
          title: '创建时间',
          type: 'text',
          column: { width: 160 },
        },
        creatorName: {
          title: '创建人',
          type: 'text',
          column: { width: 110, ellipsis: true },
        },
      },
      rowHandle: {
        fixed: 'right',
        width: 320,
        buttons: {
          view: aclRowButton({
            action: 'view',
            order: 0,
            text: '查看',
            onClick: (row) => openView?.(row),
          }),
          use: aclRowButton({
            action: 'use',
            order: 1,
            text: '使用',
            title: '以使用者身份打开验证台（非本人创建、被授「使用」时可见）',
            onClick: (row) => openValidate?.(row),
          }),
          validate: aclRowButton({
            action: 'edit',
            order: 2,
            text: '验证',
            title: '有编辑即有验证：执行者产出、检查者判定，循环到通过',
            onClick: (row) => openValidate?.(row),
          }),
          edit: aclRowButton({
            action: 'edit',
            order: 3,
            text: '编辑',
            onClick: (row) => openFormDrawer?.(row.id),
          }),
          grant: aclRowButton({
            action: 'share',
            order: 4,
            show: hasPermission('agent:orch:goal:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
          remove: aclRowButton({
            action: 'delete',
            confirm: (row: any) => `确定要删除目标「${row.name}」吗？`,
            danger: true,
            order: 5,
            text: '删除',
            onClick: (row) => removeRow?.(row),
          }),
        },
      },
    },
  };
}

function statusDict() {
  return dict({
    data: [
      { value: 1, label: '启用' },
      { value: 0, label: '停用' },
    ],
  });
}
