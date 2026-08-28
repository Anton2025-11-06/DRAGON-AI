import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
  ValueBuilderContext,
} from '@fast-crud/fast-crud';

import { useAccess } from '@vben/access';

import { dict } from '@fast-crud/fast-crud';

import {
  createTimeReadonlyColumn,
  hiddenIdColumn,
} from '#/plugin/fast-crud/shared';

import * as api from './api';

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const { resetPwd, assignRole } = props.context;
  const { hasPermission } = useAccess();
  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => await api.GetList(query),
        addRequest: async ({ form }: AddReq) => {
          const { role_ids = [], ...rest } = form;
          return await api.AddObj({ ...rest, role_ids });
        },
        editRequest: async ({ form }: EditReq) => {
          // 编辑表单不含密码字段，剩余字段直接提交（_password 仅用于剔除）
          const { password: _password, ...rest } = form;
          return await api.UpdateObj(rest);
        },
        delRequest: async ({ row }: DelReq) => await api.DelObj(row.user_id),
      },
      rowHandle: {
        width: 240,
        // 固定右侧
        fixed: 'right',
        buttons: {
          remove: { order: 3 },
          resetPassword: {
            type: 'link',
            order: 1,
            text: '重置密码',
            size: 'small',
            title: '重置密码',
            show: hasPermission('system:user:reset'),
            click({ row }: any) {
              resetPwd(row);
            },
          },
          assignRole: {
            type: 'link',
            order: 2,
            text: '分配角色',
            size: 'small',
            title: '分配角色',
            show: hasPermission('system:user:assign'),
            click({ row }: any) {
              assignRole(row);
            },
          },
        },
      },
      search: {
        // 设置搜索表单 name 前缀，避免与编辑表单 ID 冲突
        formConfig: { name: 'search' },
      },
      table: { scroll: { fixed: true } },
      columns: {
        user_id: hiddenIdColumn,
        username: {
          title: '账号',
          type: 'text',

          column: { width: 155, showTitle: true },
          search: { show: true, fixed: 'left' },
          editForm: {
            component: { disabled: true },
          },
          form: {
            rules: [
              { required: true, message: '请输入账号名' },
              { min: 3, max: 128, message: '长度在 3 到 128 个字符' },
            ],
            // 禁止浏览器自动填充已保存的 admin 账号，添加时留空由用户输入
            component: { autocomplete: 'off' },
          },
        },
        password: {
          title: '密码',
          type: 'password',
          column: { show: false },
          viewForm: {
            show: false,
          },
          addForm: {
            show: true,
          },
          editForm: {
            show: false,
          },
          form: {
            rules: [
              { required: true, message: '请输入密码' },
              { min: 6, max: 30, message: '长度在 6 到 30 个字符' },
            ],
            // new-password 提示浏览器不要自动填充已保存密码，添加时留空由用户输入
            component: { autocomplete: 'new-password' },
          },
        },
        real_name: {
          title: '真实姓名',
          type: 'text',
          column: { width: 140, ellipsis: true },
          search: { show: false },
          form: {
            rules: [{ required: true, message: '请输入真实姓名' }],
          },
        },
        phone: {
          title: '手机号',
          type: 'text',
          search: { show: false },
          column: { width: 140, align: 'center' },
          form: {
            rules: [{ required: true, message: '请输入手机号' }],
          },
        },
        email: {
          title: '邮箱',
          type: 'text',
          search: { show: false },
          column: { width: 180 },
          form: {
            rules: [{ required: true, message: '请输入邮箱' }],
          },
        },
        dept_id: {
          title: '部门',
          type: 'dict-tree',
          column: { show: false },
          search: { show: false },
          // 查看详情时不展示树选择器（避免显示部门编号），由 dept_name 列展示中文名
          viewForm: { show: false },
          dict: dict({
            isTree: true,
            url: '/api/system/depts/tree',
            value: 'dept_id',
            label: 'dept_name',
          }),
          form: {
            rules: [{ required: true, message: '请选择部门' }],
            component: {
              fieldNames: {
                children: 'children',
                label: 'dept_name',
                key: 'dept_id',
                value: 'dept_id',
              },
              showSearch: true,
              filterTreeNode: (val: any, treeNode: any) => {
                return treeNode.props.title
                  .toLowerCase()
                  .includes(val.toLowerCase());
              },
            },
          },
        },
        dept_name: {
          title: '部门',
          type: 'text',
          column: { width: 140 },
          form: { show: false },
          viewForm: { show: true },
          search: { show: false },
        },
        role_ids: {
          title: '角色',
          type: 'dict-select',
          column: { show: false },
          search: { show: false },
          addForm: { show: true },
          editForm: { show: true },
          // 列表行只返回 roles 对象数组，打开表单时回填 role_ids，查看/编辑才能回显角色名称
          valueBuilder({ row, key }: ValueBuilderContext): void {
            if (row[key] === undefined || row[key] === null) {
              row[key] = (row.roles ?? []).map((r: any) => r.role_id);
            }
          },
          dict: dict({
            url: '/api/system/roles/all',
            value: 'role_id',
            label: 'role_name',
          }),
          form: {
            rules: [{ required: true, message: '请选择角色' }],
            component: {
              mode: 'multiple',
              showSearch: true,
              filterOption: (val: string, form: any) => {
                return (
                  form?.label?.toLowerCase().indexOf(val.toLowerCase()) >= 0
                );
              },
            },
          },
        },
        roles: {
          title: '角色',
          column: {
            width: 180,
            cellRender({ record }: any) {
              const roles = record.roles ?? [];
              if (roles.length === 0) return '-';
              return roles.map((r: any) => (
                <a-tag color="blue" key={r.role_id}>
                  {r.role_name}
                </a-tag>
              ));
            },
          },
          form: { show: false },
          search: { show: false },
        },
        status: {
          title: '状态',
          search: { show: true },
          type: 'dict-radio',
          // true | false 在 渲染查询控件会有告警 antdv 问题
          valueBuilder({ value, row, key }: ValueBuilderContext): void {
            if (value !== null) {
              row[key] = value ? 1 : 0;
            }
          },
          dict: dict({
            data: [
              { value: 1, label: '启用', color: 'success' },
              { value: 0, label: '停用', color: 'error' },
            ],
          }),
          addForm: { value: 1 },
          column: { width: 80 },
        },
        create_time: createTimeReadonlyColumn,
      },
      form: {
        display: 'flex',
        group: {
          type: 'collapse', // tab
          accordion: false, // 手风琴模式
          groups: {
            baseInfo: {
              header: '基础信息',
              columns: ['username', 'password', 'real_name', 'status'],
            },
            orgInfo: {
              header: '组织信息',
              // dept_name 仅查看模式展示，必须纳入分组，否则会游离在折叠面板外
              columns: ['dept_id', 'dept_name', 'role_ids'],
            },
            linkInfo: {
              header: '联系方式',
              columns: ['phone', 'email'],
            },
          },
        },
      },
    },
  };
}
