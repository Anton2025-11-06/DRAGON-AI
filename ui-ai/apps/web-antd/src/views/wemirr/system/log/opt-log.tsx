import type {
  CreateCrudOptionsRet,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';

import { dict } from '@fast-crud/fast-crud';
import dayjs from 'dayjs';

import { defHttp } from '#/api/request';

export default function crud(): CreateCrudOptionsRet {
  return {
    crudOptions: {
      request: {
        // GET /api/system/logs?page=&page_size=&username=&module=&method=&status=&trace_id=&start_time=&end_time=
        pageRequest: async (query: any) =>
          await defHttp.get('/api/system/logs', { params: query }),
      },
      table: { scroll: { fixed: true } },
      actionbar: { show: true, buttons: { add: { show: false } } },
      rowHandle: {
        width: 80,
        // 固定右侧
        fixed: 'right',
        buttons: {
          view: { size: 'small' },
          edit: { show: false },
          remove: { size: 'small', show: false },
        },
      },
      columns: {
        log_id: {
          title: 'ID',
          type: 'text',
          column: { show: false },
          viewForm: { show: false },
        },
        trace_id: {
          title: '链路ID',
          type: 'text',
          column: { width: 190, ellipsis: true },
          search: { show: true },
        },
        username: {
          title: '操作人',
          type: 'text',
          column: { width: 120 },
          search: { show: true },
        },
        module: {
          title: '模块',
          type: 'text',
          column: { width: 110, component: { color: 'auto' } },
          search: { show: true },
        },
        operation: {
          title: '操作内容',
          type: 'textarea',
          column: { ellipsis: true, width: 260 },
          form: {
            col: { span: 24 },
          },
        },
        method: {
          title: 'HTTP方式',
          type: 'dict-select',
          column: { width: 100, component: { color: 'auto' } },
          search: { show: true },
          dict: dict({
            data: [
              { value: 'GET', label: 'GET' },
              { value: 'POST', label: 'POST' },
              { value: 'PUT', label: 'PUT' },
              { value: 'DELETE', label: 'DELETE' },
              { value: 'PATCH', label: 'PATCH' },
            ],
          }),
        },
        path: {
          title: '请求路径',
          type: 'textarea',
          column: { ellipsis: true, width: 260 },
          form: { col: { span: 24 } },
        },
        ip: {
          title: 'IP',
          type: 'text',
          column: { width: 130 },
        },
        status: {
          title: '状态',
          type: 'dict-select',
          column: {
            width: 90,
            cellRender({ text }: any) {
              const ok = Number(text) === 200;
              return ok ? (
                <a-tag color="success">正常</a-tag>
              ) : (
                <a-tag color="error">{text ?? '-'}</a-tag>
              );
            },
          },
          search: { show: true },
          dict: dict({
            data: [
              { value: 200, label: '正常', color: 'success' },
              { value: 400, label: '参数错误', color: 'warning' },
              { value: 403, label: '无权限', color: 'warning' },
              { value: 404, label: '不存在', color: 'warning' },
              { value: 500, label: '异常', color: 'error' },
            ],
          }),
        },
        cost_ms: {
          title: '耗时(ms)',
          type: 'text',
          column: { width: 100, align: 'right' },
        },
        error_msg: {
          title: '错误信息',
          type: 'textarea',
          column: { width: 200, ellipsis: true },
          form: { col: { span: 24 } },
        },
        params: {
          title: '请求参数',
          type: 'textarea',
          column: { show: false },
          form: { col: { span: 24 } },
        },
        user_agent: {
          title: 'User-Agent',
          type: 'textarea',
          column: { show: false },
          form: { col: { span: 24 } },
        },
        create_time: {
          title: '操作时间',
          type: 'datetime',
          column: { width: 170 },
          valueBuilder({ value, row, key }: ValueBuilderContext): void {
            if (value !== null && value !== undefined) {
              row[key] = dayjs(value);
            }
          },
        },
      },
      form: {
        display: 'flex',
        group: {
          type: 'collapse',
          accordion: false,
          groups: {
            baseInfo: {
              header: '基础信息',
              columns: ['trace_id', 'username', 'module', 'method', 'ip', 'status', 'cost_ms', 'create_time'],
            },
            reqInfo: {
              header: '请求信息',
              columns: ['operation', 'path', 'params', 'user_agent'],
            },
            errInfo: {
              header: '错误信息',
              columns: ['error_msg'],
            },
          },
        },
      },
    },
  };
}