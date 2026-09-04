import type {
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
} from '@fast-crud/fast-crud';

import { compute } from '@fast-crud/fast-crud';
import { message, Modal } from 'ant-design-vue';

import { applyStatusDictForAudit } from './api';
import * as api from './api';

// ==================== 通用工具 ====================

/** ISO 时间 → yyyy-MM-dd HH:mm:ss（纯字符串处理，避免时区偏移） */
const formatDateTime = (v: any): string => {
  if (!v) return '-';
  return String(v).replace('T', ' ').slice(0, 19);
};

// ==================== CRUD 配置 ====================

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  // crudExpose 位于 props 顶层（fast-crud 1.27.7），从 props.context 解构会得到 null
  const { crudExpose } = props;
  const { ApplyModal } = props.context as any;
  return {
    crudOptions: {
      request: {
        // 全局 transformQuery 输出 {page, page_size, ...form}，后端接口用 current/size -> 这里适配
        pageRequest: async (query: any) =>
          await api.AppliesPage({
            current: query.page ?? 1,
            size: query.page_size ?? 10,
            status: query.status,
            username: query.username,
            model_id: query.model_id,
          }),
      },
      table: { size: 'small', scroll: { fixed: true } },
      actionbar: {
        buttons: {
          // 审批页只在行内操作，不需要新增按钮
          add: { show: false },
        },
      },
      rowHandle: {
        show: true,
        width: 180,
        align: 'right',
        buttons: {
          view: { show: false },
          edit: { show: false },
          remove: { show: false },
          approve: {
            text: '通过',
            order: 1,
            show: compute(({ row }: any) => row.status === 0),
            async click({ row }: any) {
              Modal.confirm({
                title: '审批通过',
                content: `确认通过「${row.username}」对模型「${row.model_name}」的申请？将通过后自动创建 API Key。`,
                okText: '通过',
                cancelText: '取消',
                onOk: async () => {
                  await api.AuditApply(row.id, true);
                  message.success('已通过，API Key 已生成');
                  crudExpose.doRefresh();
                },
              });
            },
          },
          reject: {
            text: '拒绝',
            order: 2,
            show: compute(({ row }: any) => row.status === 0),
            async click({ row }: any) {
              ApplyModal(row);
            },
          },
        },
      },
      columns: {
        id: {
          title: 'ID',
          type: 'text',
          column: { show: false, width: 60 },
          form: { show: false },
        },
        model_name: {
          title: '模型',
          type: 'text',
          column: { width: 140, ellipsis: true },
          search: { show: false },
          form: { show: false },
        },
        username: {
          title: '申请人',
          type: 'text',
          column: { width: 120 },
          search: { show: true },
          form: { show: false },
        },
        dept_name: {
          title: '部门',
          type: 'text',
          column: { width: 130 },
          form: { show: false },
        },
        reason: {
          title: '申请理由',
          type: 'text',
          column: { width: 220, ellipsis: true },
          form: { show: false },
        },
        apply_time: {
          title: '申请时间',
          type: 'text',
          column: {
            width: 160,
            cellRender: ({ text }: any) => formatDateTime(text),
          },
          form: { show: false },
        },
        status: {
          title: '状态',
          // dict-select：列展示自动转中文 label，搜索自动渲染成下拉框
          type: 'dict-select',
          column: { width: 100, align: 'center' },
          search: { show: true },
          form: { show: false },
          dict: applyStatusDictForAudit(),
        },
        reject_reason: {
          title: '拒绝原因',
          type: 'text',
          column: { width: 160, ellipsis: true },
          form: { show: false },
        },
        audit_by: {
          title: '审批人',
          type: 'text',
          column: { width: 110 },
          form: { show: false },
        },
        audit_time: {
          title: '审批时间',
          type: 'text',
          column: {
            width: 160,
            cellRender: ({ text }: any) => formatDateTime(text),
          },
          form: { show: false },
        },
      },
    },
  };
}
