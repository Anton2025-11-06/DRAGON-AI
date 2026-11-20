import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { aclRowButton } from '#/plugin/fast-crud/acl-row';
import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';

/**
 * 单智能体列表配置。
 *
 * 两层权限（计划 §1）：
 * - 新建入口 show = hasPermission('agent:orch:single:add')（RBAC 全局入口）；
 * - 行级按钮（查看/编辑/删除）纯由 ACL actions 驱动，不挂 RBAC 功能权限；
 *   授权按钮是两层 AND：show = hasPermission(...grant)（入口）+ action 'share'（逐条）。
 * 不单列「验证」按钮：能编辑就能跑，验证能力已包含在「编辑与验证」这一授权类型里。
 * 「使用」只在授权弹框里作为一个授权类型存在，不是列表页的行内按钮（使用者经深度探索使用）。
 * 列表不展示修改时间（这类配置页只看创建时间就够，改动历史去操作日志查）。
 */
export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { openFormDrawer, openGrantModal, openView, removeRow } =
    props.context || {};
  const { hasPermission } = useAccess();

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) =>
          await api.PageList({
            name: query.name,
            purpose: query.purpose,
            page: query.page ?? 1,
            page_size: query.pageSize ?? 10,
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
        colSpan: 6,
      },
      // 列宽合计必须小于内容区宽度，且与 scroll.x 一致：fixed: 'right' 的操作列只有
      // 在表格显式声明了横向滚动宽度时才会按 max(列宽合计, 容器宽) 渲染，否则表格按
      // max-content 撑开，整张表（含空状态占位）被顶到容器右下方，看起来像页面漂移
      table: {
        // = 列宽合计（180+240+150+100 数据列 + 260 操作列）
        scroll: { x: 930 },
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '智能体名称',
          type: 'text',
          // 名称与用途是两个独立检索条件框（各自 like，同时填取交集）
          search: { show: true, title: '名称', component: { placeholder: '按名称检索' } },
          column: { width: 180, ellipsis: true },
        },
        purpose: {
          title: '用途',
          type: 'text',
          search: { show: true, title: '用途', component: { placeholder: '按用途检索' } },
          column: { width: 240, ellipsis: true },
        },
        create_time: {
          title: '创建时间',
          type: 'text',
          column: { width: 150 },
        },
        creatorName: {
          title: '创建人',
          type: 'text',
          column: { width: 100, ellipsis: true },
        },
      },
      rowHandle: {
        fixed: 'right',
        width: 260,
        buttons: {
          view: aclRowButton({
            action: 'view',
            order: 0,
            text: '查看',
            onClick: (row) => openView?.(row),
          }),
          edit: aclRowButton({
            action: 'edit',
            order: 1,
            text: '编辑',
            onClick: (row) => openFormDrawer?.(row.id),
          }),
          grant: aclRowButton({
            action: 'share',
            order: 2,
            show: hasPermission('agent:orch:single:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
          remove: aclRowButton({
            action: 'delete',
            confirm: (row: any) => `确定要删除智能体「${row.name}」吗？`,
            danger: true,
            order: 3,
            text: '删除',
            onClick: (row) => removeRow?.(row),
          }),
        },
      },
    },
  };
}
