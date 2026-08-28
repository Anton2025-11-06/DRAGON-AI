# AI中台前端（ui-ai）

本项目前端基于 Vben Admin 5.x（Vue 3 + TypeScript + Vite + TailwindCSS）二次开发，
完整保留其 UI 样式、风格与交互方式，仅将数据交互层适配至本项目微服务后端架构。

## 项目说明

- 前端仓库：`ui-ai/`（即本目录），原 `ui/` 目录已弃用。
- 所有请求统一发送至微服务网关 `service_gateway`，路径规范为 `/api/{service}/{path}`，
  例如系统管理 `/api/system/...`、登录认证 `/api/login/...`。
- 页面主菜单与后端业务模块绑定关系：

| 前端页面         | 后端微服务                                   |
| ---------------- | -------------------------------------------- |
| 系统管理         | service_system                               |
| 登录 / 注册 / 登出 | service_login                               |
| 智能体           | service_workflow                             |
| 知识库           | service_rag                                  |
| 模型工厂         | service_train / service_inference / service_eval_model / service_notebook |
| 数据集工厂       | service_datasets                             |

> 当前后端已开发完成：网关、系统管理、登录、注册、登出；其余业务服务开发中，
> 前端对应页面已按后端 API 契约预留占位与请求适配。

## 安装使用

```bash
# 安装依赖（需要 pnpm）
pnpm install

# 开发运行（web-antd 应用）
pnpm run dev:antd

# 打包
pnpm build
```

## 环境变量

`apps/web-antd/.env*` 中关键配置：

```bash
# 接口地址，统一走网关，由部署环境 nginx 将 /api 转发至 service_gateway
VITE_GLOB_API_URL=/api
```

## 代码提交

```bash
git add .
# 提交格式参考 internal/lint-configs/commitlint-config/index.mjs
git commit -m '提交内容'
# 未过 eslint 可通过下面命令修复
npx eslint --fix
```
