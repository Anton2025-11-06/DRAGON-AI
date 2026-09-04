import type {
  AddReq,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';

import { message } from 'ant-design-vue';

import { dict } from '@fast-crud/fast-crud';

import { defHttp } from '#/api/request';

export default function crud(): CreateCrudOptionsRet {
  return {
    crudOptions: {
      request: {
        // GET /api/system/rate-limit/configs 返回已配置策略数组
        pageRequest: async () => {
          const data = await defHttp.get('/api/system/rate-limit/configs');
          return { records: data ?? [] };
        },
        addRequest: async ({ form }: AddReq) =>
          await defHttp.post('/api/system/rate-limit/configs', form),
        editRequest: async ({ form }: EditReq) =>
          await defHttp.post('/api/system/rate-limit/configs', form),
        delRequest: async ({ row }: DelReq) =>
          await defHttp.delete(`/api/system/rate-limit/configs/${row.bucket}`),
      },
      actionbar: {
        show: true,
        buttons: {
          add: { text: '添加策略' },
          publish: {
            text: '发布到网关',
            type: 'primary',
            // 发布：通知所有存活网关从 Redis 重新加载内存限流策略
            async click() {
              const data = await defHttp.post('/api/system/rate-limit/publish');
              const { success_count = 0, failed_count = 0, policy_count = 0 } = data ?? {};
              if (failed_count > 0) {
                return message.warning(
                  `部分网关发布失败：成功 ${success_count} / 失败 ${failed_count} 个网关，策略 ${policy_count} 条`,
                );
              }
              return message.success(`已发布：成功 ${success_count} 个网关，策略 ${policy_count} 条`);
            },
          },
        },
      },
      rowHandle: {
        width: 110,
        fixed: 'right',
        buttons: {
          view: { show: false },
          edit: { text: '编辑', size: 'small' },
          remove: { size: 'small' },
        },
      },
      columns: {
        bucket: {
          title: '模块',
          type: 'dict-select',
          column: { width: 160 },
          search: { show: false },
          dict: dict({
            url: '/api/system/rate-limit/modules',
            value: 'bucket',
            label: 'name',
          }),
          editForm: {
            component: { disabled: true },
          },
          form: {
            rules: [{ required: true, message: '请选择模块' }],
          },
        },
        limit: {
          title: '窗口内最大次数',
          type: 'number',
          column: { width: 160, align: 'center' },
          form: {
            rules: [{ required: true, message: '请输入次数' }],
            component: {
              placeholder: '如 100',
              min: 1,
              max: 1000000,
              style: { width: '100%' },
            },
          },
        },
        window: {
          title: '窗口（秒）',
          type: 'number',
          column: { width: 130, align: 'center' },
          form: {
            rules: [{ required: true, message: '请输入窗口秒数' }],
            component: {
              placeholder: '如 60',
              min: 1,
              max: 86400,
              style: { width: '100%' },
            },
          },
        },
        enabled: {
          title: '是否启用',
          type: 'dict-radio',
          // 后端 JSON 解析后为 true/false，直接透传
          addForm: { value: true },
          column: { width: 110 },
          dict: dict({
            data: [
              { value: true, label: '启用', color: 'success' },
              { value: false, label: '停用', color: 'error' },
            ],
          }),
        },
      },
    },
  };
}