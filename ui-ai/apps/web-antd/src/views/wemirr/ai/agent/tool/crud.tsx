import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
} from '@fast-crud/fast-crud';

import { h } from 'vue';

import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { dict } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import CodeEditor from '../skill/components/code-editor.vue';
import * as api from './api';

export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { openTestModal, toggleStatus } = props.context || {};

  return {
    crudOptions: {
      request: {
        transformQuery: ({ page, form, sort }: any) => {
          const order =
            sort === null ? {} : { column: sort.prop, asc: sort.asc };
          return {
            current: page.currentPage ?? 1,
            size: page.pageSize ?? 10,
            ...form,
            ...order,
          };
        },
        // 编辑弹窗打开时拉取完整详情（列表接口只返回源码摘要）
        infoRequest: async ({ row }: any) => {
          return await api.GetDetail(row.id);
        },
        addRequest: async ({ form }: AddReq) => await api.AddObj(form),
        editRequest: async ({ form }: EditReq) =>
          await api.UpdateObj(form.id, form),
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.id),
      },
      toolbar: {
        buttons: {},
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '工具名称',
          type: 'text',
          search: { show: true },
          form: {
            rules: [{ required: true, message: '请输入工具名称' }],
            component: { placeholder: '请输入工具名称' },
          },
          column: { width: 180, ellipsis: true },
        },
        description: {
          title: '描述',
          type: 'textarea',
          search: { show: false },
          form: {
            col: { span: 24 },
            component: {
              placeholder: '工具用途说明（将展示给 AI 编排使用）',
              rows: 2,
              maxlength: 500,
              showCount: true,
            },
          },
          column: { width: 260, ellipsis: true },
        },
        function_code: {
          title: '函数源码',
          type: 'text',
          form: {
            col: { span: 24 },
            wrapperCol: { span: 24 },
            rules: [{ required: true, message: '请输入函数源码' }],
            component: {
              is: CodeEditor,
              vModel: 'command',
              // @ts-expect-error 透传给组件布局参数
              style: { height: '300px' },
            },
            helper:
              '定义 run() 函数或任意函数，测试时将以关键字参数调用；危险内建（文件IO/网络）已被禁用',
          },
          column: {
            width: 300,
            ellipsis: true,
            title: '源码预览',
          },
        },
        parameters_schema: {
          title: '参数说明',
          type: 'textarea',
          form: {
            col: { span: 24 },
            component: {
              placeholder:
                '{"example": {"a": 1, "b": 2}, "description": "参数说明"}',
              rows: 3,
            },
            helper:
              'JSON 格式：example 参数示例（测试弹窗自动填充）、description 参数说明',
          },
          column: { show: false },
        },
        status: {
          title: '启用状态',
          type: 'dict-switch',
          search: { show: true },
          dict: statusDict(),
          form: {
            value: true,
            component: {
              checkedChildren: '启用',
              unCheckedChildren: '禁用',
            },
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
          form: { show: false },
          column: { width: 160 },
        },
        update_time: {
          title: '更新时间',
          type: 'text',
          form: { show: false },
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
            title: '在受限环境中运行函数',
            show: true,
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
          },
          remove: {
            text: '删除',
            order: 2,
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

