import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      optimizeDeps: {
        exclude: ['@vue-office/docx', '@vue-office/excel', '@vue-office/pdf'],
      },
      server: {
        proxy: {
          // 统一转发到后端网关 service_gateway，保留 /api 前缀
          // 网关路由规范：/api/{service_name}/{path}
          '/api': {
            changeOrigin: true,
            target: 'http://localhost:18000',
            ws: true,
          },
        },
      },
    },
  };
});
