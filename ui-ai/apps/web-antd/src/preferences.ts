import { defineOverridesPreferences } from '@vben/preferences';

/**
 * @description 项目配置文件
 * 只需要覆盖项目中的一部分配置，不需要的配置不用覆盖，会自动使用默认配置
 * !!! 更改配置后请清空缓存，否则可能不生效
 */
export const overridesPreferences = defineOverridesPreferences({
  // overrides
  app: {
    name: import.meta.env.VITE_APP_TITLE,
    defaultAvatar:
      'https://unpkg.com/@vbenjs/static-source@0.1.7/source/avatar-v1.webp',
    // 登录后默认首页：深度探索 Harness 智能体（首页-深度探索）
    defaultHomePath: '/home/explorer',
    // 是否开启检查更新
    enableCheckUpdates: true,
    // 检查更新的时间间隔，单位为分钟
    checkUpdatesInterval: 5,
    accessMode: 'backend',
    contentCompact: 'wide',
    layout: 'sidebar-mixed-nav',
  },
  sidebar: {
    extraCollapse: true,
    width: 180,
  },
  logo: {
    enable: true,
    // 不配置品牌 logo，侧边栏/登录页仅展示应用名称文字
    source: '',
  },
  copyright: {
    // 仓库入口改到登录页展示（见 views/_core/authentication/login.vue）：
    // 页脚占了 32px，所有页面都没法铺平到视口底部
    companyName: 'AI中台',
    companySiteLink: '',
    enable: false,
  },
  footer: {
    enable: false,
  },
  theme: {
    mode: 'auto',
  },
});
