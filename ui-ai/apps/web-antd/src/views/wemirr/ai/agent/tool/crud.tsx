import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
} from '@fast-crud/fast-crud';

import { h } from 'vue';

import { useAccess } from '@vben/access';

import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { dict } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

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
  const { openFormModal, openTestModal, toggleStatus } = props.context || {};
  // 操作按钮按后端权限点显隐（与 workflow:tool:* 一一对应）
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
        update_time: {
          title: '更新时间',
          type: 'text',
          column: { width: 160 },
        },
      },
      rowHandle: {
        fixed: 'right',
        width: 220,
        buttons: {
          test: {
            text: '运行测试',
            type: 'link',
            size: 'small',
            icon: () => h(PlayCircleOutlined),
            title: '在受限沙箱中运行函数',
            show: hasPermission('workflow:tool:test'),
            order: 0,
            click({ row }: any) {
              if (openTestModal) {
                openTestModal(row);
              } else {
                message.warning('测试功能暂不可用');
              }
            },
          },
          edit: {
            text: '编辑',
            order: 1,
            show: hasPermission('workflow:tool:edit'),
            click({ row }: any) {
              openFormModal?.(row.id);
            },
          },
          remove: {
            text: '删除',
            order: 2,
            show: hasPermission('workflow:tool:delete'),
          },
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
