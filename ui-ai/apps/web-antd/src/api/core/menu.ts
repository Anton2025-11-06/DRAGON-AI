import type { RouteRecordStringComponent } from '@vben/types';

import { requestClient } from '#/api/request';

/** 后端菜单节点（rbac_service._menu_vo 结构） */
export interface BackendMenu {
  menu_id: number;
  parent_id: number;
  menu_name: string;
  menu_type: number; // 1 目录  2 菜单  3 按钮
  path: string;
  component: string;
  perm: string;
  icon: string;
  sort: number;
  visible: number;
  status: number;
  children?: BackendMenu[];
}

/**
 * 拼装完整路由路径：子菜单 path 为相对值时拼接父级目录 path，
 * 避免生成顶级路由导致 /system/user 等页面 404（vue-router 对非 / 开头子路径会拼父级，
 * 但 vben 动态路由的 redirect 计算、菜单高亮依赖完整绝对路径）
 */
function resolveFullPath(parentPath: string, path?: string): string {
  if (!path) return '';
  if (path.startsWith('/')) return path;
  return `${parentPath}/${path}`.replace(/\/+/g, '/');
}

/**
 * 将后端菜单树转换为 vben 路由表
 * - 过滤按钮节点（menu_type=3，权限点不参与路由）
 * - 目录（menu_type=1）使用 BasicLayout 布局
 * - 菜单（menu_type=2）component 形如 views/wemirr/xxx/index.vue
 * - 子菜单 path 为相对值时自动拼接父级目录 path
 */
export function transformMenusToRoutes(
  menus: BackendMenu[],
  parentPath = '',
): RouteRecordStringComponent[] {
  if (!Array.isArray(menus)) return [];
  const routes: RouteRecordStringComponent[] = [];
  for (const menu of menus) {
    if (menu.menu_type === 3) continue;
    // 完整路径：优先取菜单 path（相对值拼父级），否则回退 /菜单名
    const fullPath = resolveFullPath(parentPath, menu.path) || `/${menu.menu_name}`;
    const route: RouteRecordStringComponent = {
      name: `Menu${menu.menu_id}`,
      path: fullPath,
      component: 'BasicLayout',
      meta: {
        title: menu.menu_name,
        order: menu.sort,
        ...(menu.icon ? { icon: menu.icon } : {}),
      },
    };
    if (menu.menu_type === 2 && menu.component) {
      // 页面组件：形如 views/wemirr/xxx/index.vue
      route.component = menu.component;
    }
    if (menu.children?.length) {
      route.children = transformMenusToRoutes(menu.children, fullPath);
    }
    routes.push(route);
  }
  return routes;
}

/**
 * 获取当前用户可见菜单（GET /api/system/users/me/menus）
 * 返回转换后的 vben 路由表
 */
export async function getAllMenusApi() {
  const menus = await requestClient.get<BackendMenu[]>(
    '/api/system/users/me/menus',
  );
  return transformMenusToRoutes(menus);
}

/**
 * 获取全部菜单资源树（菜单管理页面使用）
 */
export async function getResourceTree() {
  return requestClient.get<BackendMenu[]>('/api/system/menus/tree');
}
