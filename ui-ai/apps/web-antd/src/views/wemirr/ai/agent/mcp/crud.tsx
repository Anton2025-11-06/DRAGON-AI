import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
} from '@fast-crud/fast-crud';

import { h } from 'vue';

import { useAccess } from '@vben/access';

import { ApiOutlined, ToolOutlined } from '@ant-design/icons-vue';
import { dict } from '@fast-crud/fast-crud';
import { message, Modal } from 'ant-design-vue';

import { aclRowButton } from '#/plugin/fast-crud/acl-row';
import { hiddenIdColumn, statusDict } from '#/plugin/fast-crud/shared';

import * as api from './api';

export default function createCrudOptions(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { openGrantModal, openToolsModal } = props.context || {};
  // 操作按钮两层 AND：功能权限管这类事让不让你做，行上的 actions 管这一条你能不能做
  const { hasPermission } = useAccess();

  /** 按已保存的连接测一次：会真的外连第三方，所以后端卡的是「测试连接」 */
  async function testConnection(row: any) {
    const hide = message.loading('正在测试连接...', 0);
    try {
      const result = await api.TestConnection(row.id);
      hide();
      if (result.success) {
        Modal.success({
          content: h('div', [
            h('p', `服务器: ${result.serverName}`),
            h('p', `可用工具数量: ${result.toolCount}`),
            h('p', `响应时间: ${result.responseTime}ms`),
          ]),
          title: '连接测试成功',
        });
      } else {
        Modal.error({
          content: h('div', [
            h('p', { style: { color: 'red' } }, result.errorMessage),
            h('p', `响应时间: ${result.responseTime}ms`),
          ]),
          title: '连接测试失败',
        });
      }
    } catch (error: any) {
      hide();
      Modal.error({
        content: error.message || '未知错误',
        title: '连接测试失败',
      });
    }
  }

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          return await api.PageList(query);
        },
        addRequest: async ({ form }: AddReq) => await api.AddObj(form),
        editRequest: async ({ form }: EditReq) =>
          await api.UpdateObj(form.id, form),
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.id),
      },
      toolbar: {
        buttons: {},
      },
      actionbar: {
        buttons: {
          add: { show: hasPermission('workflow:mcp:add') },
        },
      },
      form: {
        wrapper: {
          buttons: {
            // 表单底部“测试连接”按钮：放到确定(ok, order=5)左边，按当前表单参数测试，无需保存
            testConnection: {
              text: '测试连接',
              order: 4.5,
              async click({ form }: any) {
                const hide = message.loading('正在测试连接...', 0);
                try {
                  const result = await api.TestParams(form);
                  hide();
                  if (result.success) {
                    Modal.success({
                      title: '连接测试成功',
                      content: h('div', [
                        h('p', `服务器: ${result.serverName}`),
                        h('p', `可用工具数量: ${result.toolCount}`),
                        h('p', `响应时间: ${result.responseTime}ms`),
                      ]),
                    });
                  } else {
                    Modal.error({
                      title: '连接测试失败',
                      content: h('div', [
                        h(
                          'p',
                          { style: { color: 'red' } },
                          result.errorMessage,
                        ),
                        h('p', `响应时间: ${result.responseTime}ms`),
                      ]),
                    });
                  }
                } catch (error: any) {
                  hide();
                  Modal.error({
                    title: '连接测试失败',
                    content: error.message || '未知错误',
                  });
                }
              },
            },
          },
        },
      },
      columns: {
        id: hiddenIdColumn,
        name: {
          title: '服务名称',
          type: 'text',
          search: { show: true },
          form: {
            rules: [{ required: true, message: '请输入服务名称' }],
            component: {
              placeholder: '请输入服务名称',
            },
          },
          column: { width: 180, ellipsis: true },
        },
        description: {
          title: '描述',
          type: 'textarea',
          form: {
            component: {
              placeholder: '描述这个 MCP 连接是做什么的',
              rows: 2,
            },
            col: { span: 24 },
            helper: '用于说明该 MCP 提供的能力，便于其他用户理解',
          },
          column: { width: 200, ellipsis: true, show: false },
        },
        type: {
          title: '连接类型',
          type: 'dict-radio',
          dict: dict({
            data: [
              { value: 'SSE', label: 'SSE', color: 'green' },
              { value: 'STDIO', label: 'STDIO', color: 'blue' },
            ],
          }),
          addForm: {
            value: 'SSE',
          },
          column: {
            width: 120,
          },
        },
        command: {
          title: 'STDIO命令',
          type: 'text',
          form: {
            component: {
              placeholder: '例如: npx',
            },
            helper: 'STDIO模式下的可执行命令',
          },
          column: { show: false },
        },
        args: {
          title: 'STDIO参数',
          type: 'textarea',
          form: {
            component: {
              placeholder: '例如: ["@modelcontextprotocol/server-filesystem"]',
              rows: 3,
            },
            col: { span: 24 },
            helper: 'JSON数组格式，例如: ["arg1", "arg2"]',
          },
          column: { show: false },
          // 将JSON字符串转换为格式化的文本用于显示
          valueBuilder({ value, row, key }: any) {
            if (value && typeof value === 'string') {
              try {
                JSON.parse(value);
                row[key] = value; // 保持原始JSON字符串
              } catch {
                row[key] = value;
              }
            }
          },
        },
        url: {
          title: 'SSE URL',
          type: 'text',
          form: {
            // rules: [
            //   { required: true, message: '请输入SSE URL', trigger: 'change' },
            // ],
            component: {
              placeholder: '例如: http://localhost:3000/sse',
            },
            helper: 'SSE模式下的连接URL',
          },
          column: { show: false },
        },
        env: {
          title: '环境变量',
          type: 'textarea',
          form: {
            col: { span: 24 },
            component: {
              placeholder: '例如: {"API_KEY": "your-key", "DEBUG": "true"}',
              rows: 3,
            },
            helper: 'JSON对象格式，例如: {"KEY": "value"}',
          },
          column: { show: false },
        },
        status: {
          title: '启用状态',
          type: 'dict-switch',
          form: {
            value: true,
            component: {
              checkedChildren: '启用',
              unCheckedChildren: '禁用',
            },
          },
          dict: statusDict(),
          column: {
            width: 120,
          },
        },
        // 创建人：按钮置灰时得知道找谁要授权（展示名由列表接口批量翻好）
        creatorName: {
          title: '创建人',
          type: 'text',
          column: { width: 110, ellipsis: true },
          form: { show: false },
          search: { show: false },
        },
      },
      rowHandle: {
        fixed: 'right',
        width: 320,
        buttons: {
          edit: aclRowButton({
            action: 'edit',
            order: 0,
            show: hasPermission('workflow:mcp:edit'),
            text: '编辑',
            // 自绘按钮走不到内置 edit 的默认行为，显式打开编辑表单
            onClick: (row, scope) =>
              props.crudExpose?.openEdit({ index: scope?.index, row }),
          }),
          remove: aclRowButton({
            action: 'delete',
            danger: true,
            order: 1,
            show: hasPermission('workflow:mcp:delete'),
            text: '删除',
            onClick: (row) => {
              Modal.confirm({
                content: `确定要删除MCP服务"${row.name}"吗？`,
                okType: 'danger',
                onOk: async () => {
                  await props.crudExpose?.doRemove(
                    { row },
                    { noConfirm: true },
                  );
                  message.success('删除成功');
                },
              });
            },
          }),
          testConnection: aclRowButton({
            action: 'test',
            icon: ApiOutlined,
            order: 2,
            show: hasPermission('workflow:mcp:test-external'),
            text: '测试连接',
            title: '测试MCP连接',
            onClick: (row) => void testConnection(row),
          }),
          viewTools: aclRowButton({
            action: 'tools',
            icon: ToolOutlined,
            order: 3,
            show: hasPermission('workflow:mcp:toolList'),
            text: '查看工具',
            title: '查看MCP工具',
            onClick: (row) => openToolsModal?.(row),
          }),
          grant: aclRowButton({
            action: 'share',
            order: 4,
            show: hasPermission('workflow:mcp:grant'),
            text: '授权',
            onClick: (row) => openGrantModal?.(row),
          }),
        },
      },
    },
  };
}
