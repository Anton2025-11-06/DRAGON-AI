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
 * 路由记录（component 可缺）：中间层目录不给 component，由 vue-router 的 RouterView
 * 跳过无组件的层级，直接子页面渲染进顶级 BasicLayout 的内容区
 */
type MenuRoute = Omit<RouteRecordStringComponent, 'component'> & {
  component?: string;
};

/**
 * 将后端菜单树转换为 vben 路由表
 * - 过滤按钮节点（menu_type=3，权限点不参与路由）
 * - 目录（menu_type=1）只有顶级才挂 BasicLayout：accessible.ts 仅对顶级路由剔除
 *   component，中间层目录也挂上就会在布局里再渲染一套页头/菜单，子菜单页与菜单栏之间
 *   会多出整屏空白
 * - 菜单（menu_type=2）component 形如 views/wemirr/xxx/index.vue
 * - 子菜单 path 为相对值时自动拼接父级目录 path
 */
export function transformMenusToRoutes(
  menus: BackendMenu[],
  parentPath = '',
): RouteRecordStringComponent[] {
  if (!Array.isArray(menus)) return [];
  const routes: MenuRoute[] = [];
  for (const menu of menus) {
    if (menu.menu_type === 3) continue;
    // 完整路径：优先取菜单 path（相对值拼父级），否则回退 /菜单名
    const fullPath = resolveFullPath(parentPath, menu.path) || `/${menu.menu_name}`;
    // 页面用自带组件；目录只在顶级兜底 BasicLayout，中间层不给组件以免多套一层布局
    let component: string | undefined;
    if (menu.menu_type === 2) {
      component = menu.component || undefined;
    } else if (menu.menu_type === 1 && !parentPath) {
      component = 'BasicLayout';
    }
    const route: MenuRoute = {
      name: `Menu${menu.menu_id}`,
      path: fullPath,
      ...(component ? { component } : {}),
      meta: {
        title: menu.menu_name,
        order: menu.sort,
        ...(menu.icon ? { icon: menu.icon } : {}),
      },
    };
    if (menu.children?.length) {
      route.children = transformMenusToRoutes(menu.children, fullPath);
    }
    routes.push(route);
  }
  return routes as RouteRecordStringComponent[];
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
