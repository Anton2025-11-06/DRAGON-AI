import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { dict } from '@fast-crud/fast-crud';

import { aclRowButton } from '#/plugin/fast-crud/acl-row';
import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';

/**
 * 工具列表配置
 * 新增/编辑走自定义弹窗（ToolFormModal，排版对齐工作流代码节点，含「按定义测试」），
 * 因此这里只保留列表列与删除/状态切换，不再配置 fast-crud 内置表单。
 */
export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const {
    openFormModal,
    openGrantModal,
    openTestModal,
    removeRow,
    toggleStatus,
  } = props.context || {};
  // 操作按钮两层 AND：功能权限管这类事让不让你做，行上的 actions 管这一条你能不能做
  const { hasPermission } = useAccess();

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          return await api.PageList(query);
        },
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.id),
      },
      actionbar: {
        buttons: {
          add: {
            show: hasPermission('workflow:tool:add'),
            click() {
              openFormModal?.(null);
            },
          },
        },
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '工具名称',
          type: 'text',
          search: { show: true },
          column: { width: 180, ellipsis: true },
        },
        description: {
          title: '描述',
          type: 'text',
          column: { width: 240, ellipsis: true },
        },
        function_code: {
          title: '源码预览',
          type: 'text',
          column: { width: 300, ellipsis: true },
        },
        timeout: {
          title: '超时(ms)',
          type: 'text',
          column: { width: 96 },
        },
        status: {
          title: '启用状态',
          type: 'dict-switch',
          search: { show: true },
          dict: statusDict(),
          component: {
            checkedChildren: '启用',
            unCheckedChildren: '禁用',
          },
          valueChange({ row, value }: any) {
            if (row?.id !== undefined) {
              toggleStatus?.(row, Boolean(value));
            }
          },
          column: { width: 100 },
        },
        create_time: {
          title: '创建时间',
          type: 'text',
          column: { width: 160 },
        },
        // 创建人：按钮置灰时得知道找谁要授权（展示名由列表接口批量翻好）
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
        width: 260,
        buttons: {
          // 运行测试跑的是真代码，卡的是页面按钮对应的「运行测试」动作
          test: aclRowButton({
            action: 'test',
            icon: PlayCircleOutlined,
            order: 0,
            show: hasPermission('workflow:tool:test'),
            text: '运行测试',
            title: '在受限沙箱中运行函数',
            onClick: (row) => openTestModal?.(row),
          }),
          edit: aclRowButton({
            action: 'edit',
            order: 1,
            show: hasPermission('workflow:tool:edit'),
            text: '编辑',
            onClick: (row) => openFormModal?.(row.id),
          }),
          grant: aclRowButton({
            action: 'share',
            order: 2,
            show: hasPermission('workflow:tool:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
          remove: aclRowButton({
            action: 'delete',
            confirm: (row: any) => `确定要删除工具「${row.name}」吗？`,
            danger: true,
            order: 3,
            show: hasPermission('workflow:tool:delete'),
            text: '删除',
            onClick: (row) => removeRow?.(row),
          }),
        },
      },
    },
  };
}

/** 启用状态字典（布尔） */
function statusDict() {
  return dict({
    data: [
      { value: true, label: '启用' },
      { value: false, label: '禁用' },
    ],
  });
}
