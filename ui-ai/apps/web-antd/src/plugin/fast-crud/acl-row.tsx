/**
 * fast-crud 行内按钮的 ACL 门禁。
 *
 * 两层 AND，缺一不可：
 * - `show`（调用方传 hasPermission(...) 的结果）：功能权限，管「这类事让不让你做」；
 * - `action` + 行上的 `actions`：数据权限，管「这一条你能不能做」。
 * 只有前者会出现「有工具管理权的人，删除按钮对别人的工具也亮着」；
 * 只有后者会出现「角色没这个按钮权限，但行上是自己的数据所以按钮亮着，点了 403」。
 *
 * 判定规则一律读后端下发的 actions（见 canAction），前端不复制第二份。
 */
import type { AclActionableRow } from '#/api/acl';

import { h } from 'vue';

import { compute } from '@fast-crud/fast-crud';
import { Modal } from 'ant-design-vue';

import { canAction } from '#/api/acl';

/** 按钮点击时可拿到的行上下文（fast-crud 的 render/click 作用域） */
export interface AclRowScope {
  index?: number;
  row?: AclActionableRow;
}

export interface AclRowButtonOptions {
  /** 数据权限动作码（后端 RESOURCE_SPECS 里的码，如 edit/delete/share/test） */
  action: string;
  /** 二次确认：给文案（或返回文案）就先用确认框拦一下，危险操作必须给 */
  confirm?: ((row: any) => string) | string;
  danger?: boolean;
  disabled?: boolean | ((row: any) => boolean);
  /** 图标组件（@ant-design/icons-vue 的组件，不是 iconify 字符串） */
  icon?: any;
  /** 点击回调：这里给的是整行对象，不是 fast-crud 的 { row, index } 包装 */
  onClick: (row: any, scope?: AclRowScope) => any;
  order?: number;
  /** 功能权限结果（调用方传 hasPermission(...)）；默认放行 */
  show?: boolean;
  size?: 'large' | 'middle' | 'small';
  text: string;
  /** 悬停说明：用原生 title 属性，不额外套 tooltip（按钮排在一格里） */
  title?: string;
  type?: 'default' | 'link' | 'primary' | 'text';
}

export function aclRowButton(
  options: AclRowButtonOptions,
): Record<string, any> {
  const {
    action,
    confirm,
    danger = false,
    disabled,
    icon,
    onClick,
    order = 0,
    show = true,
    size = 'small',
    text,
    title,
    type = 'link',
  } = options;

  async function fire(row: any, scope?: AclRowScope) {
    const tip = typeof confirm === 'function' ? confirm(row) : confirm;
    if (tip) {
      Modal.confirm({
        content: tip,
        okType: danger ? 'danger' : 'primary',
        onOk: async () => await onClick(row, scope),
        title: '请确认',
      });
      return;
    }
    await onClick(row, scope);
  }

  return {
    order,
    // 行级显隐：compute 每行算一次，别人拿不到的行直接没这个按钮
    show: compute(({ row }: any) => show && canAction(row, action)),
    render(scope: AclRowScope) {
      const row = scope?.row;
      const isDisabled =
        typeof disabled === 'function' ? Boolean(disabled(row)) : Boolean(disabled);
      // title 不在 antd Button 的 props 声明里，用展开传过去，避免 JSX 属性类型报错
      const extra: Record<string, any> = title ? { title } : {};
      return (
        <a-button
          danger={danger}
          disabled={isDisabled}
          size={size}
          type={type}
          v-slots={icon ? { default: () => text, icon: () => h(icon) } : { default: () => text }}
          onClick={() => void fire(row, scope)}
          {...extra}
        />
      );
    },
  };
}
