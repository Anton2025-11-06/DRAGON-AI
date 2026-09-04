import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  UserPageQuery,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';

import dayjs from 'dayjs';

import { defHttp } from '#/api/request';

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  // 注意：crudExpose 是 props 顶层属性，props.context 是用户自定义上下文（不含 crudExpose）
  const { crudExpose } = props;
  return {
    crudOptions: {
      request: {
        // GET /api/system/online 分页查询（transformQuery 自动携带 page/page_size/keyword）
        pageRequest: async (query: UserPageQuery) =>
          await defHttp.get('/api/system/online', { params: query }),
        // 强制下线 = 删除该登录态（DELETE /api/system/online/{jti}）
        delRequest: async ({ row }: DelReq) =>
          await defHttp.delete(`/api/system/online/${row.jti}`),
      },
      table: { scroll: { fixed: true } },
      actionbar: { show: true, buttons: { add: { show: false } } },
      rowHandle: {
        width: 90,
        fixed: 'right',
        buttons: {
          view: { show: false },
          edit: { show: false },
          remove: {
            size: 'small',
            // 覆盖全局默认的“删除”按钮文案，改为“强制下线”
            render(scope: any) {
              function confirm() {
                const { row, index } = scope;
                crudExpose.doRemove({ row, index }, { noConfirm: true });
              }
              return (
                <a-popconfirm
                  cancel-text="取消"
                  ok-text="确认下线"
                  onConfirm={confirm}
                  placement="bottom"
                  title="确定要强制该用户下线吗？"
                >
                  <fs-button class="ant-btn-sm" danger type="link">
                    强制下线
                  </fs-button>
                </a-popconfirm>
              );
            },
          },
        },
      },
      columns: {
        keyword: {
          title: '关键词',
          type: 'text',
          column: { show: false },
          form: { show: false },
          search: {
            show: true,
            component: { placeholder: '账号/真实姓名/部门/角色' },
          },
        },
        jti: {
          title: '登录态ID',
          type: 'text',
          column: { show: false },
          form: { show: false },
        },
        user_id: {
          title: '用户ID',
          type: 'text',
          column: { width: 80, align: 'center' },
          form: { show: false },
        },
        username: {
          title: '账号',
          type: 'text',
          column: { width: 155 },
          form: { show: false },
        },
        real_name: {
          title: '真实姓名',
          type: 'text',
          column: { width: 130 },
          form: { show: false },
        },
        dept_name: {
          title: '部门',
          type: 'text',
          column: { width: 140 },
          form: { show: false },
        },
        roles: {
          title: '角色',
          type: 'text',
          column: {
            width: 200,
            cellRender({ record }: any) {
              const roles = record.roles ?? [];
              if (roles.length === 0) return '-';
              return roles.map((r: any) => (
                <a-tag color="blue" key={r}>
                  {r}
                </a-tag>
              ));
            },
          },
          form: { show: false },
        },
        login_at: {
          title: '登录时间',
          type: 'datetime',
          column: { width: 170 },
          valueBuilder({ value, row, key }: ValueBuilderContext): void {
            if (value !== null && value !== undefined) {
              row[key] = dayjs.unix(Number(value));
            }
          },
        },
        expire_at: {
          title: '过期时间',
          type: 'datetime',
          column: { width: 170 },
          valueBuilder({ value, row, key }: ValueBuilderContext): void {
            if (value !== null && value !== undefined) {
              row[key] = dayjs.unix(Number(value));
            }
          },
        },
      },
    },
  };
}
