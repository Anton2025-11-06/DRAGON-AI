# CLAUDE.md

Claude Code 应与 `AGENTS.md` 一起使用本文件。`AGENTS.md` 是本仓库 agent 规则主来源；本文件只是 Claude 高频执行摘要。新增长期项目规则时，先更新 `AGENTS.md`，再同步本文件中 Claude 需要反复遵循的部分。

如果两份文件存在差异，优先遵循 `AGENTS.md` 中更完整、更具体的项目规则。如果用户当前指令与这些文件冲突，遵循用户当前指令；当取舍重要时，说明原因和影响。

本文件基于 `multica-ai/andrej-karpathy-skills` 的 Karpathy 风格 agent 指南，并结合本前端仓库做了本地化：不要过度假设、不要过度设计、不要改无关代码、用真实验证证明目标行为。

## 第一原则

1. 编辑前先思考。
   - 非平凡改动前，先阅读附近代码和一个相似实现。
   - 涉及 API、权限、路由、菜单、FastCrud、i18n 时，先查现有契约。
   - 多步骤任务先给出简短计划。

2. 保持方案小而直接。
   - 优先实现最简单的正确方案。
   - 现有 Vben、Ant Design Vue、FastCrud、`@vben/*` 能解决时，不引入新依赖。
   - 只有代码库明显需要时才新增抽象。

3. 做有边界的改动。
   - 只修改完成任务所需的文件。
   - 不清理无关 TODO、注释、import、格式或 warning。
   - 不删除你没有理解的注释或代码。

4. 明确验证。
   - 每个非平凡改动都需要具体检查。
   - 注释/文档类改动运行 `git diff --check`。
   - 前端代码改动优先运行覆盖目标应用的最窄 typecheck/lint/build 命令。
   - 如果验证因环境或工具链问题失败，准确说明失败命令和原因判断。

## 项目上下文

Wemirr Platform UI 是 Vue 3 + Vite + TypeScript + Vben Admin 5 + Ant Design Vue + Pinia + Vue Router + Vue I18n + FastCrud + Turbo + pnpm workspace 的前端 monorepo。

重要目录：

- `apps/web-antd`：主应用。
- `apps/web-antd/src/views/wemirr`：WEMIRR 业务页面，包含 platform、system、workflow、WMS、TMS、AI/RAG、develop。
- `apps/web-antd/src/api`：请求客户端、认证接口和公共 API。
- `apps/web-antd/src/router`：路由、动态菜单、权限守卫。
- `apps/web-antd/src/plugin/fast-crud`：FastCrud 全局配置、按钮权限、上传和表格公共行为。
- `packages`、`packages/@core`、`packages/effects`：Vben 共享能力。

重要约定：

- 使用 Vue 3 Composition API 和 `<script setup lang="ts">`。
- 统一使用 `requestClient`、`defHttp`、`baseRequestClient`，不要在业务页面中新建裸 `axios`。
- 认证、刷新 token、`Authorization`、`x-request-id`、`Accept-Language` 和错误提示由请求拦截器处理。
- 动态菜单和可访问路由主要来自后端，`preferences.app.accessMode = 'backend'`。
- FastCrud 页面先读同目录 `index.vue`、`crud.ts`/`crud.tsx`、`api.ts`，再修改。
- 按钮权限优先使用 `setup-fast-crud-permission.ts` 的 `permission.prefix` / `prefix:action` 模式。
- API 字段、枚举值、状态码、分页参数、权限码属于前后端契约。修改前查后端或相似页面。
- 当前 WEMIRR 业务 CRUD 文案以中文为主；通用路由/布局文案优先使用 `$t` 和 `apps/web-antd/src/locales`。
- 不要记录或展示密码、token、clientSecret、私钥或完整认证响应。

品牌与版权保护：

- 不要移除或替换 `Wemirr`、`WEMIRR-PLATFORM`、`battcn`、`Vben`、README 作者/文档/演示/交流群链接。
- 保留 MIT license、Vben upstream 信息和现有作者信息。

## 常用命令

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

文档/注释改动检查：

```bash
git diff --check
```

新建未跟踪文档时可额外检查：

```bash
git diff --no-index --check /dev/null AGENTS.md
git diff --no-index --check /dev/null CLAUDE.md
```

## 工作流

L0 任务：

- 查看相关文件。
- 直接做局部改动。
- 运行聚焦检查。
- 总结改动文件和验证结果。

L1/L2 任务：

- 从 `git status --short` 开始。
- 用 `rg` 定位相关页面、API、路由、权限和相似实现。
- 设计前先读附近实现。
- 分小批编辑。
- 关键改动后验证。
- 报告残余风险或未验证区域。

## 领域预读

进入下列领域前，先读核心文件和至少一个相似页面：

- 认证/权限/菜单：`apps/web-antd/src/api/core/auth.ts`、`apps/web-antd/src/router/access.ts`、`apps/web-antd/src/router/guard.ts`、`apps/web-antd/src/preferences.ts`。
- 请求/上传/导出：`apps/web-antd/src/api/request.ts`、`apps/web-antd/src/api/helper.ts`、`apps/web-antd/src/plugin/fast-crud/setup-fast-crud.tsx`。
- FastCrud 页面：目标目录下 `index.vue`、`crud.ts`/`crud.tsx`、`api.ts`。
- 系统/平台：关注权限、租户、字典、i18n、OSS、消息。
- WMS：关注收货、库存、容器、储位、流水、子表刷新和状态变更。
- TMS：关注订单、车辆、司机、维修、费用、结算规则字段。
- Workflow：关注流程分类、模型、实例、任务列表和办理状态。
- AI/RAG：关注 SSE、会话、模型配置、知识库、文档上传、向量化状态和异步刷新。
- Develop：关注代码生成、在线表单、打印设计、网关配置等高配置化页面。

## Claude 专用工具建议

- 优先用 `rg`，再考虑更慢的搜索工具。
- 优先直接读取相关文件，避免一次性加载过多上下文。
- 使用精确 patch 编辑；除非必要，不要整文件重写。
- 仅在任务彼此独立且不共享可变状态时使用 subagent。
- 除非用户明确要求，不要使用破坏性 shell 命令。

## 验证与诚实

除非相关命令或检查刚刚成功，否则不要说“已修复”“已通过”“完成”等同义表述。

验证选择：

- 文档/注释：`git diff --check`。
- 单业务页面：`pnpm -F @vben/web-antd run typecheck`。
- API、权限、路由：typecheck 加相关 lint。
- shared package、工程配置：`pnpm run check:type` 或更高层级检查。
- 构建链路、依赖、生产行为：`pnpm run build:antd`。

验证失败时，按下面结构回复：

- 命令：
- 结果：
- 可能原因：
- 影响：
- 下一步：

## 示例

### 新增业务列表页

好的做法：

- 阅读同模块 `index.vue`、`crud.tsx`、`api.ts`。
- 确认后端分页、权限码、字典和字段类型。
- 做最小新增。
- 运行 `pnpm -F @vben/web-antd run typecheck`。

### 修复接口字段

好的做法：

- 查后端 DTO 和已有调用。
- 更新 API 类型和页面字段。
- 避免用多个字段名兼容来掩盖契约不一致。

### 调整权限

好的做法：

- 同时检查后端菜单权限码、FastCrud `permission.prefix`、路由守卫和操作按钮展示。
- 不只隐藏按钮，也要确认操作入口没有绕过权限。
