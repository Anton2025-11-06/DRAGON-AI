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
 * 目标列表配置（与单/团队两层权限口径一致，资源编码 orch_goal）：
 * - 新建入口 show = hasPermission('agent:orch:goal:add')（RBAC 全局入口）；
 * - 行级按钮（查看/编辑/删除）纯由 ACL actions 驱动；
 *   授权两层 AND：show = hasPermission('agent:orch:goal:grant') + action 'share'。
 * 「使用」只是授权弹框里的一个授权类型，不是列表页的行内按钮。
 * 不单列「验证」按钮：能编辑就能跑，验证能力已经包含在「编辑与验证」这一授权类型里。
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
      // 同单智能体页：列宽合计收敛到内容区宽度内，并显式给出 scroll.x，避开
      // 固定右侧操作列把整张表顶到容器右下方造成的页面漂移
      table: {
        // = 列宽合计（160+200+110+110+150+100 数据列 + 260 操作列）
        scroll: { x: 1090 },
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '目标名称',
          type: 'text',
          // 名称与用途是两个独立检索条件框（各自 like，同时填取交集）
          search: { show: true, title: '名称', component: { placeholder: '按名称检索' } },
          column: { width: 160, ellipsis: true },
        },
        purpose: {
          title: '用途',
          type: 'text',
          search: { show: true, title: '用途', component: { placeholder: '按用途检索' } },
          column: { width: 200, ellipsis: true },
        },
        executorName: {
          title: '执行者',
          type: 'text',
          column: { width: 110, ellipsis: true },
        },
        verifierName: {
          title: '检查者',
          type: 'text',
          column: { width: 110, ellipsis: true },
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
            show: hasPermission('agent:orch:goal:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
          remove: aclRowButton({
            action: 'delete',
            confirm: (row: any) => `确定要删除目标「${row.name}」吗？`,
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
