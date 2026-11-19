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
 * 单智能体列表配置。
 *
 * 两层权限（计划 §1）：
 * - 新建入口 show = hasPermission('agent:orch:single:add')（RBAC 全局入口）；
 * - 行级按钮（查看/使用/编辑/删除/验证）纯由 ACL actions 驱动，不挂 RBAC 功能权限；
 *   授权按钮是两层 AND：show = hasPermission(...grant)（入口）+ action 'share'（逐条）。
 * 编辑/验证都读 actions 里的 'edit'（有编辑即有验证）。
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
            show: hasPermission('agent:orch:single:add'),
            click() {
              openFormDrawer?.(null);
            },
          },
        },
      },
      form: {
        // 顶部搜索：名称/用途合并成一个关键词框（后端 keyword 同时 like name/purpose）
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
          title: '智能体名称',
          type: 'text',
          column: { width: 180, ellipsis: true },
        },
        purpose: {
          title: '用途',
          type: 'text',
          column: { width: 240, ellipsis: true },
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
        update_time: {
          title: '更新时间',
          type: 'text',
          column: { width: 160 },
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
            title: '以使用者身份打开对话（非本人创建、被授「使用」时可见）',
            onClick: (row) => openValidate?.(row),
          }),
          validate: aclRowButton({
            action: 'edit',
            order: 2,
            text: '验证',
            title: '有编辑即有验证：左侧配置镜像 + 右侧对话边改边验',
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
            show: hasPermission('agent:orch:single:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
          remove: aclRowButton({
            action: 'delete',
            confirm: (row: any) => `确定要删除智能体「${row.name}」吗？`,
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
