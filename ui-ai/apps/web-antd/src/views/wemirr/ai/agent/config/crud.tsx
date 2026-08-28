import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  EditReq,
} from '@fast-crud/fast-crud';

import { dict } from '@fast-crud/fast-crud';

import { createTimeColumn, hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';
import AvatarUpload from './components/AvatarUpload.vue';

export default function crud(
  _props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          return await api.pageList(query);
        },
        addRequest: async ({ form }: any) => {
          return await api.create(form);
        },
        editRequest: async ({ form }: EditReq) => {
          return await api.modify(form.id, form);
        },
        delRequest: async ({ row }: any) => {
          return await api.remove(row.id);
        },
      },
      // 完全禁用表格，使用卡片布局
      table: {
        show: false,
        scrollToFirstRowOnChange: false,
      },
      pagination: {
        show: true,
      },
      actionbar: {
        buttons: {
          add: {
            text: '新增智能体',
            show: true, // 启用默认新增按钮
          },
          export: { show: false },
          import: { show: false },
        },
      },
      form: {},
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '名称',
          type: 'text',
          search: { show: true },
          form: {
            rules: [{ required: true, message: '请输入智能体名称' }],
            component: {
              placeholder: '请输入智能体名称',
            },
          },
          column: { width: 200 },
        },
        avatar: {
          title: '头像',
          type: 'cropper-uploader',
          form: {
            show: true,
            component: {
              is: AvatarUpload,
            },
            // col: { span: 24 },
          },
          column: {
            width: 80,
            component: {
              name: 'fs-avatar',
              vModel: 'src',
              size: 'small',
            },
          },
        },
        modelId: {
          title: '绑定模型',
          type: 'dict-select',
          search: { show: false },
          form: {
            rules: [{ required: true, message: '请选择绑定模型' }],
            component: {
              placeholder: '请选择模型',
              allowClear: true,
            },
          },
          dict: dict({
            url: '/api/workflow/models/list',
            value: 'id',
            label: 'name',
          }),
          column: { width: 150 },
        },
        description: {
          title: '描述',
          type: 'textarea',
          form: {
            col: { span: 24 },
            wrapperCol: { span: 24 },
            component: {
              placeholder: '请输入智能体描述',
              rows: 3,
              maxlength: 500,
              showCount: true,
            },
          },
          column: {
            width: 300,
            ellipsis: true,
          },
        },
        systemPrompt: {
          title: '角色预设',
          type: 'textarea',
          column: { show: false },
          form: {
            col: { span: 24 },
            component: {
              placeholder: '请输入智能体的角色预设信息',
              rows: 4,
              maxlength: 2000,
              showCount: true,
            },
          },
        },
        kbId: {
          title: '关联知识库',
          type: 'dict-select',
          form: {
            component: {
              placeholder: '请选择知识库',
              allowClear: true,
            },
          },
          dict: dict({
            getData: async () => {
              try {
                const res = await api.getKnowledgeBaseList();
                return res.records || [];
              } catch (error) {
                console.error('获取知识库列表失败:', error);
                return [];
              }
            },
            value: 'id',
            label: 'name',
          }),
          column: { width: 120 },
        },
        tools: {
          title: '工具配置',
          type: 'dict-select',
          column: {
            show: false,
          },
          form: {
            component: {
              mode: 'multiple',
              placeholder: '请选择工具',
              allowClear: true,
            },
          },
          dict: dict({
            getData: async () => {
              try {
                const res = await api.getTools();
                return res || [];
              } catch (error) {
                console.error('获取工具列表失败:', error);
                return [];
              }
            },
            value: 'beanName',
            label: 'beanName',
          }),
          // 值构建器：将JSON字符串解析为数组用于表单显示
          valueBuilder({ value, row, key }: any) {
            if (typeof value === 'string' && value) {
              try {
                const parsed = JSON.parse(value);
                if (Array.isArray(parsed)) {
                  row[key] = parsed;
                  return;
                }
              } catch {
                // 如果不是有效JSON，设为空数组
              }
            }
            row[key] = [];
          },
          // 值解析器：表单提交时将数组转换为JSON字符串存储
          valueResolve({ value }: any) {
            if (Array.isArray(value) && value.length > 0) {
              return JSON.stringify(value);
            }
            return ''; // 返回空字符串而不是null
          },
        },
        createName: {
          title: '创建人',
          type: 'text',
          form: { show: false },
          column: { width: 120 },
        },
        createTime: createTimeColumn,
      },
    },
  };
}
