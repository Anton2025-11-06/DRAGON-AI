import type { RouteRecordRaw } from 'vue-router';

import { preferences } from '@vben/preferences';

import { BasicLayout } from '#/layouts';

const routes: RouteRecordRaw[] = [
  {
    component: BasicLayout,
    path: '/',
    redirect: preferences.app.defaultHomePath,
    children: [],
  },
];

export default routes;
