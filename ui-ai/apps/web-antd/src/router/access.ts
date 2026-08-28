import type {
  ComponentRecordType,
  GenerateMenuAndRoutesOptions,
  RouteRecordRaw,
} from '@vben/types';

import { generateAccessible } from '@vben/access';
import { preferences } from '@vben/preferences';

import { message } from 'ant-design-vue';

import { getAllMenusApi } from '#/api';
import { BasicLayout, IFrameView } from '#/layouts';
import { $t } from '#/locales';

const forbiddenComponent = () => import('#/views/_core/fallback/forbidden.vue');

/**
 * 隐藏业务路由（不进菜单，登录后可用，供页面内部跳转）
 * 路径与后端菜单种子的 path 保持一致：/agent/workflow/editor/:id 等
 */
const hiddenBusinessRoutes: RouteRecordRaw[] = [
  {
    name: 'AgentChat',
    path: '/agent/chat',
    component: () => import('#/views/wemirr/ai/chat/agent/index.vue'),
    meta: { hideInMenu: true, title: '智能体对话' },
  },
  {
    name: 'WorkflowEditor',
    path: '/agent/workflow/editor/:id?',
    component: () => import('#/views/wemirr/ai/workflow/editor/index.vue'),
    meta: { hideInMenu: true, title: '工作流编辑' },
  },
  {
    name: 'WorkflowHistory',
    path: '/agent/workflow/history/:id?',
    component: () => import('#/views/wemirr/ai/workflow/history/index.vue'),
    meta: { hideInMenu: true, title: '执行历史' },
  },
];

async function generateAccess(options: GenerateMenuAndRoutesOptions) {
  const pageMap: ComponentRecordType = import.meta.glob('../views/**/*.vue');

  const layoutMap: ComponentRecordType = {
    BasicLayout,
    IFrameView,
  };

  const { accessibleMenus, accessibleRoutes } = await generateAccessible(
    preferences.app.accessMode,
    {
      ...options,
      fetchMenuListAsync: async () => {
        message.loading({
          content: `${$t('common.loadingMenu')}...`,
          duration: 1.5,
        });
        return await getAllMenusApi();
      },
      // 可以指定没有权限跳转403页面
      forbiddenComponent,
      // 如果 route.meta.menuVisibleWithForbidden = true
      layoutMap,
      pageMap,
    },
  );

  // 动态菜单生成后，挂载隐藏业务路由到根布局（不参与菜单展示）
  for (const route of hiddenBusinessRoutes) {
    if (!options.router.hasRoute(route.name as string)) {
      options.router.addRoute('Root', route);
    }
  }

  return { accessibleMenus, accessibleRoutes };
}

export { generateAccess };
