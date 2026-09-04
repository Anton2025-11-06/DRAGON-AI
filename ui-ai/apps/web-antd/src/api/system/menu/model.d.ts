/**
 * @description: 菜单资源树节点（菜单管理/权限分配使用）
 * menuType: C 菜单 / F 按钮 / M 目录
 */
export interface MenuOption {
  id: number;
  parentId?: number;
  label: string;
  menuType: 'C' | 'F' | 'M';
  icon?: string;
  orderNum?: number;
  path?: string;
  component?: string;
  visible?: boolean;
  status?: number;
  children?: MenuOption[];
}