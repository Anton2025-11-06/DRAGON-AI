# AGENTS.md

本文件是 Codex 以及其他 coding agent 在本前端仓库工作的项目级操作指南。

它综合了：

- 当前仓库的 Vben 5 / Vue 3 / pnpm monorepo 约定。
- 配套后端 `wemirr-platform` 的 agent 规则风格。
- `multica-ai/andrej-karpathy-skills` 中的 Karpathy 风格 coding-agent 规则：先澄清再实现、保持简单、只改必要范围、用真实验证证明结果。

## 项目概览

Wemirr Platform UI 是基于 Vue 3、Vite、TypeScript、Vben Admin 5、Ant Design Vue、Pinia、Vue Router、Vue I18n、FastCrud、Turbo 和 pnpm workspace 的前端 monorepo。

核心目录：

- `apps/web-antd`：当前主要应用，WEMIRR 业务页面、API、路由、i18n、Vite 配置都在这里。
- `apps/web-antd/src/views/wemirr`：业务页面，包含 platform、system、workflow、WMS、TMS、AI/RAG、develop 等模块。
- `apps/web-antd/src/api`：全局 API、请求客户端、认证接口和公共 helper。
- `apps/web-antd/src/router`：路由、动态菜单、权限守卫。
- `apps/web-antd/src/plugin/fast-crud`：FastCrud 全局配置、权限按钮、上传和表格公共行为。
- `apps/web-antd/src/locales`：前端国际化资源。
- `packages`、`packages/@core`、`packages/effects`：Vben 基础包、UI kit、hooks、request、stores、layouts 等共享能力。
- `internal`：lint、tsconfig、vite-config、tailwind-config 等内部工程配置。

## 核心工作规则

- 先思考，再编辑。非平凡任务开始前，先识别目标页面、API 契约、路由/权限、状态流、验证方式和风险。
- 保持简单。优先沿用 Vben、Ant Design Vue、FastCrud 和项目已有模式，不新增无必要抽象或依赖。
- 外科手术式改动。不要格式化、重排、重命名或重构无关文件。
- 保护用户已有工作。较大改动前先看 `git status --short`；除非用户明确要求，绝不回退不是你做的改动。
- 管理不确定性。接口字段、权限码、菜单路径、后端返回结构或业务状态不清楚时，先查现有页面和后端契约；仍无法确认时再问。
- 用证据验证。没有刚刚执行的命令或直接检查结果时，不要声称 lint、typecheck、build 或修复已通过。
- 暴露真实失败。不要用假数据、吞异常、静默降级或硬编码成功态掩盖接口、权限、构建或类型问题。

## 规则源维护

- `AGENTS.md` 是本仓库 agent 规则主来源；`CLAUDE.md` 是 Claude Code 面向执行的摘要。
- 新增、删除或调整长期项目规则时，先更新 `AGENTS.md`，再把 Claude 需要高频遵循的内容同步到 `CLAUDE.md`。
- 如果本文件、`CLAUDE.md` 和用户当前指令冲突，优先遵循用户当前指令；同时说明冲突点、取舍原因和影响。
- 不要把一次性任务背景写成永久规则。只有能长期约束本仓库开发质量的约定才加入本文件。

## 品牌与版权保护

- 不要移除、替换或弱化 `Wemirr`、`WEMIRR-PLATFORM`、`battcn`、`Vben`、模块名、包名、README 作者/文档/演示/交流群链接，除非用户明确要求。
- 保留 MIT license、Vben upstream 信息和现有作者信息。不要把项目对外标识改成通用模板名。
- README、路由标题、菜单文案和业务页面当前以中文为主。不要批量改成英文。

## 任务分级

使用与风险匹配的最轻流程。

- L0：文档、注释、单页面小修、单 API 类型修正。读取相关文件，局部修改，运行聚焦检查。
- L1：新增/修改业务页面、API、FastCrud 配置、路由、权限、i18n 或共享组件。收集相似实现，简短规划，小步编辑，验证目标应用。
- L2：跨 package 工程配置、请求拦截器、权限守卫、构建配置、主题布局、认证、上传、SSE/AI 流式交互或大范围页面迁移。必要时记录关键决策和验证细节。

## 常用命令

本仓库使用 pnpm workspace，根 `package.json` 要求：

- Node.js `>=20.12.0`
- pnpm `>=10.0.0`
- 当前 package manager：`pnpm@10.22.0`

安装依赖：

```bash
pnpm install
```

本地开发：

```bash
pnpm run dev:antd
```

聚焦检查：

```bash
pnpm -F @vben/web-antd run typecheck
pnpm run lint
pnpm run check:type
pnpm run build:antd
```

全量检查：

```bash
pnpm run check
pnpm run build
pnpm run test:unit
```

文档/注释类改动：

```bash
git diff --check
```

如果只新增未跟踪文档，普通 `git diff --check` 可能看不到内容，可使用：

```bash
git diff --no-index --check /dev/null AGENTS.md
git diff --no-index --check /dev/null CLAUDE.md
```

交付说明中必须报告实际执行的命令和结果。命令失败时，不要改口说已通过；记录失败命令、关键错误、可能原因和下一步判断。

## TypeScript 与 Vue 风格

- 使用 Vue 3 Composition API 和 `<script setup lang="ts">`，遵循现有 Vben 5 写法。
- 优先复用 `@vben/*` workspace 包、`@vueuse/core`、Ant Design Vue、FastCrud 和已有业务组件。
- API 类型尽量放在同模块 `api.ts` 或 `model.d.ts` 中，与已有页面保持一致。
- 不要为了单个页面引入新的状态管理、请求库、日期库、表格库或表单库。
- 避免 `any` 扩散。现有 FastCrud callback 中已有 `any` 时可以局部沿用，但新增接口、响应、表单和枚举应尽量定义明确类型。
- 不要直接操作 DOM 或全局变量，除非编辑器、打印、文件预览、图谱或第三方组件确实需要，并说明边界。
- 不要把业务逻辑塞进模板表达式。复杂逻辑放到 `computed`、函数、hooks 或配置文件中。
- 注释用于解释业务规则、不变量、权限意图、接口差异、第三方组件取舍或非显然边界。不要添加复述代码的噪音注释。

## 请求、认证与接口契约

- 统一使用 `apps/web-antd/src/api/request.ts` 中的 `requestClient`、`defHttp`、`baseRequestClient`。不要在业务页面里新建裸 `axios` 实例。
- 认证、刷新 token、`Authorization`、`x-request-id`、`Accept-Language` 和错误提示由请求拦截器统一处理。不要在单个页面重复实现。
- 后端成功码、响应字段和分页结构遵循当前 `defaultResponseInterceptor` 与 FastCrud `transformRes` 约定。
- 分页参数默认使用 `current`、`size`、`offset`、`column`、`asc` 等现有约定。修改前先查同模块 API 和后端 DTO。
- 下载、上传、Blob、SSE、AI 流式接口等特殊请求要复用现有 helper 或相似实现，例如 `commonExport`、FastCrud uploader、AI chat API。
- Feign/后端 DTO 字段、枚举值、状态码、权限码属于前后端契约。修改前查后端、菜单配置和已有页面。
- 不要记录或展示密码、token、clientSecret、私钥、敏感配置或完整认证响应。

## 路由、菜单与权限

- 本项目使用 `preferences.app.accessMode = 'backend'`，菜单和可访问路由主要来自后端。
- 动态页面映射由 `import.meta.glob('../views/**/*.vue')` 提供。新增后端菜单组件路径时，必须与 `apps/web-antd/src/views` 下真实文件路径匹配。
- 权限守卫位于 `apps/web-antd/src/router/guard.ts`，菜单生成位于 `apps/web-antd/src/router/access.ts`。修改前先读这两个文件。
- FastCrud 按钮权限使用 `apps/web-antd/src/plugin/fast-crud/setup-fast-crud-permission.ts`，权限码通常按 `prefix:action` 拼接。
- 不要绕过 `useAccess`、`useAccessStore`、`useUserStore`、`useAuthStore` 的现有职责。
- 新增页面时同时考虑菜单可见性、后端权限码、路由标题、keep-alive、面包屑、403 和登录重定向。

## FastCrud 与业务页面

- WEMIRR 业务列表页大量使用 FastCrud。新增或修改 CRUD 页面前，先读同模块相似的 `index.vue`、`crud.ts`/`crud.tsx`、`api.ts`。
- `pageRequest`、`addRequest`、`editRequest`、`delRequest` 的参数形状要与后端接口一致，不要为了页面方便改坏契约。
- 表格列宽、固定列、搜索项、表单校验、字典、弹窗/抽屉布局要遵循已有页面密度和交互习惯。
- 字典优先复用 FastCrud `dict`、现有 `useDict`、table-select helper 或后端字典接口，不要散落重复静态枚举。
- 删除操作默认保留确认提示。批量、导出、上传、状态变更等高影响操作必须让结果和失败可见。
- 如果页面包含嵌套维护抽屉、子表、库存/收货/结算/向量化等异步流程，刷新和状态同步要显式处理。

## 国际化与文案

- 全局路由、布局和通用文案优先使用 `$t` 与 `apps/web-antd/src/locales`。
- 当前 WEMIRR 业务 CRUD 配置大量使用中文标题和校验提示。修改时保持同模块一致；不要在一次任务里强行全量 i18n 化。
- 如果新增通用菜单、布局或核心页面文案，应同步 `zh-CN` 和 `en-US`，并检查 key 命名是否与现有结构一致。
- 不要把后端返回的业务错误翻译成另一套前端硬编码文案，除非产品明确要求。

## UI 与可用性

- 这是后台管理系统，界面应保持紧凑、清晰、可扫描，优先效率和一致性。
- 使用 Ant Design Vue、Vben 组件、FastCrud 和现有 `page-layout-card` / `fs-page` 结构，不要随意引入营销页式布局。
- 图标优先使用现有 Iconify/Ant Design 图标体系。
- 表格、表单、抽屉、弹窗、上传、预览、编辑器等控件要考虑 loading、empty、error、disabled、permission denied 和 mobile/窄屏基本表现。
- 文本不得溢出按钮、表头、标签或弹窗标题。长文本列优先使用 ellipsis、tooltip、固定宽度或可复制组件。
- 不要新增大面积单色渐变、装饰背景或与当前 Vben 管理台风格冲突的视觉元素。

## 领域预读清单

进入下列领域前，先读对应前端核心文件和至少一个后端契约或相似页面。

- 认证/权限/菜单：`apps/web-antd/src/api/core/auth.ts`、`apps/web-antd/src/router/access.ts`、`apps/web-antd/src/router/guard.ts`、`apps/web-antd/src/preferences.ts`。
- 请求/上传/导出：`apps/web-antd/src/api/request.ts`、`apps/web-antd/src/api/helper.ts`、`apps/web-antd/src/plugin/fast-crud/setup-fast-crud.tsx`。
- FastCrud 页面：目标目录下 `index.vue`、`crud.ts`/`crud.tsx`、`api.ts`，以及同业务模块相似页面。
- 系统/平台：`apps/web-antd/src/views/wemirr/system`、`apps/web-antd/src/views/wemirr/platform`，关注权限、租户、字典、i18n、OSS、消息。
- WMS：`apps/web-antd/src/views/wemirr/wms`，关注收货、库存、容器、储位、流水、子表刷新和状态变更。
- TMS：`apps/web-antd/src/views/wemirr/tms`，关注订单、车辆、司机、维修、费用、结算规则字段。
- Workflow：`apps/web-antd/src/views/wemirr/workflow`，关注流程分类、模型、实例、任务列表和办理状态。
- AI/RAG：`apps/web-antd/src/views/wemirr/ai`，关注 SSE、会话、模型配置、知识库、文档上传、向量化状态和异步刷新。
- Develop：`apps/web-antd/src/views/wemirr/develop`，关注代码生成、在线表单、打印设计、网关配置等高配置化页面。

## 验证规则

选择与改动风险匹配的检查。

- 文档/注释改动：运行 `git diff --check`；新文件可额外使用 `git diff --no-index --check /dev/null <file>`。
- 单业务页面改动：至少运行 `pnpm -F @vben/web-antd run typecheck`，必要时启动 `pnpm run dev:antd` 做页面检查。
- API 类型、请求封装、权限、路由改动：运行 `pnpm -F @vben/web-antd run typecheck` 和相关 lint。
- shared package、工程配置、lint/tsconfig/vite/turbo 改动：运行 `pnpm run check:type` 或更高层级检查。
- 构建链路、依赖、动态导入、路由或生产行为改动：运行 `pnpm run build:antd`。
- 组件、hooks、utils 有单测覆盖时运行 `pnpm run test:unit` 或对应 Vitest 聚焦命令。

如果无法完成验证，必须报告：

- 尝试执行的命令。
- 失败现象。
- 可能原因。
- 对当前改动的风险。
- 建议下一步。

## 安全边界

没有用户明确批准，不要执行：

- `git commit`、`git push`、删除分支、强推或改写历史。
- 任务范围外的破坏性文件系统操作。
- 批量替换路由、菜单、权限码、接口前缀或环境变量。
- 生产、远程或外部账号操作。
- secret 轮换、权限变更或凭证内容输出。

默认允许在相关任务中执行：

- 读取文件、搜索代码、查看日志、运行本地构建/测试、编辑项目文件、使用 `git status`/`git diff`。

## Agent 回复风格

- 简洁但具体。
- 优先说明改了什么、验证了什么。
- 有帮助时带上文件路径。
- 如实说明不确定性和残余风险。
- 不要掩盖构建、lint、typecheck 或测试失败。

## 良好行为示例

示例：新增业务列表页。

- 不好：直接复制一个页面，改几个标题后跳过权限、字典和接口类型。
- 好：先读同模块 `index.vue`、`crud.tsx`、`api.ts`，确认后端分页/权限码/字典，再做最小新增并运行 typecheck。

示例：修复接口字段。

- 不好：在页面里兼容多个字段名，默认展示空字符串。
- 好：查后端 DTO 和已有调用，确认真实字段；必要时补类型并让异常状态可见。

示例：调整权限。

- 不好：隐藏按钮但仍允许调用操作接口。
- 好：同时检查后端菜单权限码、FastCrud `permission.prefix`、路由守卫和操作按钮展示。
